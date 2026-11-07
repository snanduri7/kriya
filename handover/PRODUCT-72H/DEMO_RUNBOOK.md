# Owner visual walkthrough - product/demo-72h

**Status of everything below: FUNCTIONALLY WIRED / OWNER VISUAL REVIEW PENDING.** Nothing here is "visually approved"; that is your call after this walkthrough.
Evidence words: **LIVE** = I clicked it in the real Electron app against a real model and saw the result (evidence paths in SPRINT_LOG.md sections 5-7); **TESTED** = covered by automated tests, not clicked; **NOT AVAILABLE** = stated honestly in the product.

## 0. Launch (one command)

```
handover/PRODUCT-72H/walkthrough/launch.sh --reset
```

It checks the model server (qwen3-coder:30b, nomic-embed-text) and Docker, builds the cockpit from this tree, creates everything under `~/kriya-walkthrough/` (outside the repo, own state directory, mode 0700), seeds two demo repos from `walkthrough/seeds/`, and starts the app at the Home screen. It never touches a repo of yours, never enables outward lookup, never pushes. `--reset` starts Project Memory from empty (use it before every full pass). Quit the app with Cmd+Q. `--prepare-only` checks the kit without starting the app.

Folders you will choose in the app (the script puts `~/kriya-walkthrough` on your clipboard; in a folder dialog press Cmd+Shift+G and paste, then add the folder name):

| Demo | Folder |
|---|---|
| New Project parent | `~/kriya-walkthrough/projects` (empty) |
| Brownfield Python | `~/kriya-walkthrough/brownfield-python` (a calculator, one commit) |
| Java / Maven | `~/kriya-walkthrough/java-maven` (a Greeter class + JUnit 5, one commit) |

Open the REAL folder, never a copy (a copy is a different repository identity and Project Memory would not carry over). Do not start a run while any test run is using Docker.

## 1. Numbered checklist

Expected wording is what I saw in the real app; if you see something different, that is a defect to record (section 4).

**1. HOME.** App opens on the Home screen with "New Project..." and an open-existing-folder action. (LIVE)

**2. GREENFIELD (New Project).** New Project... -> parent `~/kriya-walkthrough/projects`, name `greeter-demo` -> Create project. Goal:
`Create greet.py with a function greet(name) that returns "Hello, <name>!" and test_greet.py with two pytest tests for it.`
Start Run -> live Planning -> Editing -> Validating -> **Approve & Continue** on the diff -> success, 2 files created, 2/2 tests. (LIVE)
*Runtime-verified variant (shows the second approval):* a new project with `Create hello.py. It prints hello world when executed. Include a pytest test for it.` -> after the diff approval the app asks **Continue to Run Approval...**, then a native confirmation -> Runtime row PASS. LIVE for the variant without the test sentence (1 pass; earlier attempts hit a transient venv fault, mitigated by a bounded retry, registry WAVE8-PY-RUNTIME-ONLY-VENV-BOOTSTRAP-001); the variant with the sentence is LIVE through the CLI. Always include the test sentence in front of an audience.
Disclosed on that approval card: it shows the exact command Kriya approved, which for Python can be Kriya's own interpreter path; the card says the interpreter is replaced by the project environment's own when it runs (registry RUNTIME-APPROVED-ARGV-VS-EXECUTED-INTERPRETER-001).

**3. BROWNFIELD + RUN COCKPIT.** Open `brownfield-python`. Run 1 goal:
`Add a function percent(part, whole) to calc.py that returns part as a percentage of whole rounded to one decimal place, and add a pytest test for it.`
Watch: elapsed time, Planning -> Editing -> Validating, Current activity line, then **Approve & Continue** (the diff is shown first), then the result with "Created/Changed" files, Build / Tests / Runtime / Safety checks / Security / Recovery rows. (LIVE, twice.)
A model call can occasionally run long: one Reviewer call once took 7.5 minutes (it ran to its 16,384-token output cap, registry REVIEWER-OUTPUT-CAP-RUNAWAY-LATENCY-001). Elapsed time and the Validating phase keep showing; if it ever stalls on stage, open a finished run from Runs (Replay, item 12).

**4. PROJECT MEMORY (Python).** Project page -> "Verified Project Memory": after run 1 "Learned last run 3" (language Python, build system pip, evidence requirements.txt). Run 2 goal:
`Add a function multiply(a, b) to calc.py that returns the product of a and b, and add a pytest test for it.`
-> "Reused last run 3" and the run's own Overview line "Verified Project Memory: Reused 3". "Why does Kriya know this?" shows the evidence file and the run that taught it. **Stale:** after run 2, in a terminal: `echo "# demo" >> ~/kriya-walkthrough/brownfield-python/requirements.txt`, then Project -> the three facts read STALE / EVIDENCE_CHANGED. (Learn and reuse LIVE in the app; stale LIVE at the backend projection, `~/kriya-evidence/product-72h-env/memory-proof/`.)

**5. PROJECT MEMORY (Java / Maven).** Open `java-maven`. Run 1 goal (names the package and file on purpose, see section 3):
`Add a method farewell(String name) to the existing Greeter class in src/main/java/demo/Greeter.java that returns "Goodbye, <name>!", and add a JUnit 5 test for it in src/test/java/demo/GreeterTest.java.`
Learned 4 (language Java, build system Maven, release 17, module). Run 2 goal: `Add a method shout(String name) to the existing Greeter class in src/main/java/demo/Greeter.java that returns the greeting in upper case, and add a JUnit 5 test for it in src/test/java/demo/GreeterTest.java.` -> reused 4. **Stale:** change `<maven.compiler.release>17` to `21` in `pom.xml` (do not commit) -> 4 facts STALE. (LIVE; evidence `~/kriya-evidence/product-72h-env/java-memory-proof/`, the runs there were started from the terminal and the Electron screens opened on that repository.)

**6. CAPABILITY STRIP (project page, then a run's Overview).** Cells: Local model, Code intelligence, Language server, Skills, Tools, Security, Private state, Outward lookup, Verify, Recovery, Learning. Expected: Local model "Answering; configured models are served" when idle and **"Not checked while a run is in progress"** during a run (the probe is deliberately paused so the run keeps the service); Code intelligence Available; **Language server Unavailable (JDTLS_NOT_CONTAINABLE)**; Outward lookup **Disabled**; Security in force; Learning N current. A run's Overview shows per-run states (USED / ACTIVE / Not recorded) with their evidence.
Customer wording for the language server, which the product states from its own health row and this document only quotes: Kriya currently provides contained structural code intelligence; Java LSP integration is disabled until it can meet the same containment boundary. Code intelligence is the structural index, not an LSP.

**7. CODE INTELLIGENCE / SKILLS / TOOLS.** Skills and Tools pages from the sidebar: skills list with the "1 unverified" count; Tools shows 0 MCP servers and "Not checked ... this page never starts one". On a finished run, the Code intelligence cell reads USED when known-target context was recorded. (LIVE)

**8. SECURITY.** Project page -> Security card: "What Kriya refuses - 6 of 8 checks blocked", each with the typed reason code and rule. These are **live checks of Kriya's real file protections against situations built in a disposable folder**, run when the page loads: real refusals, never recorded history and never your project. (LIVE) Per-run Security in a result reads "Not recorded" with the write scope when one was recorded: per-run records carry no ALLOWED/BLOCKED verdict, so none is invented.

**9. LOOKUP.** Outward lookup shows **Disabled** (default). **OUTWARD LOOKUP PRODUCT PATH INCOMPLETE** - do not demonstrate or claim a live lookup: inside the desktop app the per-query approval is declined by design, the cockpit has no lookup approval card, and run evidence has no lookup record (registry DESKTOP-LOOKUP-QUERY-APPROVAL-001). Nothing was enabled; the SearXNG container is untouched.

**10. VERIFY / RECOVERY.** Result rows Build / Tests / Runtime and the Recovery row ("1 attempt (succeeded)", or "8 attempts, #1-#7 failed, #8 succeeded" on the real repair run); Attempts and Timeline pages show each attempt. (LIVE)

**11. WHY / EVIDENCE.** Open a finished run -> WHY tab: each gate has its purpose label and how a baseline was handled. (LIVE)

**12. REPLAY.** Runs -> open any finished run: the result is labelled "Replay of Run <id> - the result below is read from the evidence Kriya stored for this run, and opening it re-executes nothing". It is a label on the stored view; there is no separate "replay mode" in the host. Absent for a row stored as in progress. (LIVE)

**13. SETTINGS.** Settings -> read-only configuration overview (what is set, from where). (LIVE)

## 2. Honest limits (read before presenting)
- **Language server:** unavailable by design under mandatory containment (jdtls has no contained mode); say so as a security-strength statement.
- **Outward lookup:** Disabled; path incomplete (item 9). Not part of this demo.
- **Containment is mandatory with no opt-out**; the Security card and the runtime card state it from Kriya's own health row.
- **Per-run Security** reads "Not recorded" (write scope only); the ALLOWED/BLOCKED/REDACTED vocabulary appears on the project Security card only.
- **Runtime approval card** shows the approved command, which can be a host interpreter path for Python; the card says the project environment's interpreter replaces it. A Java runtime card would show `mvn ...`; no Java run in my evidence reached a runtime approval, so I have not seen one.
- **Local model cell** reads "Not checked" while a run is in progress (by design).
- Project Memory facts are language, build system, Java release, modules and which verification gates passed - never commands. It is not model training.
- **Reproducibility:** the model is local and non-deterministic. A run can fail closed (that is a result, not a crash). Never rerun an identical failed goal hoping it passes; note it as a finding.
- Pre-existing and unrelated: `ui/standalone/test/catalog_real.test.ts` skills.list (fails identically on the untouched baseline).

## 3. Java demo disclosure
The Java goals above name the package and file on purpose. The same repository with a goal that names only the class (`Add a method farewell(String name) to Greeter ...`) failed closed after 8 attempts because the model wrote `src/main/java/Greeter.java` and dropped the `demo/` package directory (registry JAVA-DEVELOPER-WRONG-PACKAGE-PATH-001, P3, model behaviour). **Do not present the Java run as Kriya correcting that behaviour.** It stays P3 unless it repeats under a goal with clear context.

## 4. Defects found during your walkthrough (record only real ones; visible ones are P0/P1)

| # | Step | What you saw | Expected | Visible to a customer? |
|---|---|---|---|---|
| | | | | |

## 5. Not done / waiting
- No `snapshot/demo-candidate-local-2026-11-07` tag yet: created only after this walkthrough and any demo-blocking fixes. Nothing is pushed.
- Scenario-04 (OI-125) runs on the final tip after the walkthrough, with you and the live model.
- No full test suite exists on any tip of this branch (owner ruling: none now).
