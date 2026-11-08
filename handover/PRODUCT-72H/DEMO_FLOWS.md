# Demo flows (prepared 2026-11-07, product/demo-72h) - FUNCTIONALLY WIRED / OWNER VISUAL REVIEW PENDING

Every duration, file list and count below was MEASURED in a real run on this tree unless it says UNPROVEN. The model is local and non-deterministic: **never rerun an identical failed goal hoping it passes** (use the fallback in each flow).
Start the app with `handover/PRODUCT-72H/walkthrough/launch.sh` (add `--reset` ONLY before a full pass: it wipes `~/kriya-walkthrough`, including the stored runs the Replay fallback uses).

## 0. Honest-evidence states the audience should see
- **Requirement truth (new 2026-11-08).** The result's headline is the backend's typed requirement truth. *Run completed* + "3 of 3 requirements verified" = every requirement was closed by deterministic evidence (the hello-world goal below, LIVE). *Changes applied - verification incomplete* + "N of M requirements verified - K unverified" + the unresolved requirements = the change was applied and some requirement has no independent evidence (every goal with a GENERAL behaviour statement, e.g. the greet and percent goals). Say which one you are showing; it is the product telling the truth, not a failure.
- **Observed output.** Under Verification, *Runtime verification* shows the command Kriya ran, the output it printed and the exit status, from the recorded run (redacted, capped; lost output is said).
- **How to run** appears on a successful result. It shows a command only when Kriya itself ran the application and saw it succeed; otherwise it says *"Kriya did not run this application, so it has no verified run command."* Both are correct, say which one you are showing.
- **Requirement verification**: most successful runs still end with their requirement *unverified* (a model's "satisfied" is a claim, not evidence). The Result says "Formal requirement verification" under Verification (collapsed). That is the product telling the truth about evidence; the closer for "a test was added to an existing test file" is fixed, the behaviour closer does not exist yet (registry REQUIREMENT-BEHAVIOR-CLAIM-NO-DETERMINISTIC-CLOSER-001).
- The **Kriya Log** tab is Kriya's real application log, not a summary. Activity is the summary.

## DEMO 1 - GREENFIELD (new project -> watch -> approve -> result)
- **Start:** `launch.sh` -> Home -> New Project... -> parent `~/kriya-walkthrough/projects` (empty), name `greeter-demo` (a NEW name every pass; never reuse a folder).
- **Goal (exact):** `Create greet.py with a function greet(name) that returns "Hello, <name>!" and test_greet.py with two pytest tests for it.`
- **Expected (MEASURED, 3 live runs):** 100-122 s. Stepper: Planning -> Implementing (the approval card appears here, ~45-70 s in) -> Verifying -> Complete. **Approve & Continue** on the diff. Result: *Changes applied - verification incomplete* (the goal's return value is a placeholder, so the behaviour statement has no deterministic authority: 0 of 1 requirements verified - say so), 2 files created (`greet.py +2 −0`, `test_greet.py +8 −0`), Verification: Build passed, 2 / 2 tests passed. How to run: *no verified run command* (this goal states no runtime obligation). **For a result that reads *Run completed* with 3 of 3 verified, use the hello-world variant below.**
- **Show during the run:** the stepper moving; the dock's **Kriya Log** tab (real lines: model request, "Developer: generating content for 'greet.py'", the REVIEW banner, "Escalating changes to human approval gate").
- **Open in Finder** shows the two files. Nothing is committed to Git (Kriya never commits your work).
- **Variant with a verified run command (needs the presenter's click):** goal `Create hello.py. It prints hello world when executed. Include a pytest test for it.` -> after the diff approval the app asks **Continue to Run Approval...**, then a NATIVE confirmation that automation cannot press -> the Runtime row passes -> **How to run** shows `python3 hello.py` with Copy and Open Terminal Here, the headline reads *Run completed* with *3 of 3 requirements verified*, and *Runtime verification* shows the observed output `hello world`, exit status 0 (LIVE CLI proof of the closure: `~/kriya-evidence/product-72h-env/closer-live-1`). Backend proof of the recipe: `~/kriya-evidence/product-72h-env/recipe-live-1/` (see SPRINT_LOG section 10). The card shows the effective command (`.kriya/venv/bin/python hello.py`): that is exactly what runs after you approve.
- **Fallback if the model diverges / fails:** do not rerun. Runs -> open **Replay of Run 1ee786b0** (the same goal, succeeded in the walkthrough state) and say it is a stored real run.

## DEMO 2 - SECOND GOAL / CONTINUITY (Kriya sees and edits what it built)
- **Start:** directly after Demo 1, same project. Click **New Goal** (the box is blank).
- **Goal (exact):** `Change the greet function in greet.py to return "Hi, <name>!" instead, and update its tests to match.`
- **Expected (MEASURED live, ~/kriya-evidence/product-72h-env/two-goal-1):** ~90 s. Result: *2 files changed*, `greet.py +1 −1`, `test_greet.py +2 −2`, **no new file**, 2 / 2 tests passed; the folder holds exactly `greet.py` and `test_greet.py`, and `greet.py` now returns `"Hi, {name}!"`. Before the fix a goal like this created a substitute `app.py` (the Planner never saw the first run's file).
- **What to say:** "Kriya shows the planner and the reviewer the project as it is now - including files it created that Git does not track yet - so the second goal edits the same file."
- **Fallback:** Replay of the two-goal evidence runs: launch with `KRIYA_STATE_DIR=~/kriya-evidence/product-72h-env/two-goal-1/state` and open the two stored runs in Runs.

## DEMO 3 - BROWNFIELD (existing repo, code intelligence, project memory)
- **Start:** Open Folder -> `~/kriya-walkthrough/brownfield-python` (a calculator repo, one commit).
- **Goal 1 (exact):** `Add a function percent(part, whole) to calc.py that returns part as a percentage of whole rounded to one decimal place, and add a pytest test for it.`
- **Expected (MEASURED 117 s):** diff first, then the result; Project page -> Verified Project Memory: *Learned last run 3* (language Python, build system pip, evidence requirements.txt). The goal names its file, so Code Intelligence finds it.
- **Goal 2 (reuse):** `Add a function multiply(a, b) to calc.py that returns the product of a and b, and add a pytest test for it.` -> *Reused last run 3*. (The first multiply run in the walkthrough ended in the now-fixed optional-runtime stop, run 304c8d2e; a rerun with this tree is expected to pass but is UNPROVEN live.)
- **Fallback:** Replay of Run 76123f00 (percent, succeeded).

## DEMO 4 - VERIFIED PROJECT MEMORY (learn -> reuse -> stale)
- Python: runbook section 4. After run 2, in a terminal `echo "# demo" >> ~/kriya-walkthrough/brownfield-python/requirements.txt`, then Project -> the three facts read STALE / EVIDENCE_CHANGED.
- Java: runbook section 5 (`java-maven`; goals name package and file on purpose). Fallback: Replay of Run bce4bee6 (farewell, succeeded).
- **Reset:** `launch.sh --reset` (Project Memory starts empty). Say plainly: memory facts are language, build system, release, modules and which gates passed - never commands.

## DEMO 5 - SECURITY (a real refusal, with the reason, no secrets)
- **Project page -> Security card:** "What Kriya refuses" - live checks of Kriya's real file protections against situations built in a disposable folder (never your project): typed reason code and rule per check, no secret values.
- **A real refusal in the product:** make a folder that is not empty and not a Git repo: `mkdir /tmp/not-empty && echo hi > /tmp/not-empty/README.md`; Open Folder -> that folder; goal `Create greet.py with a function greet(name)`. Expected (MEASURED): refused before any model call with the card *"Kriya can't start this new project because the folder already contains files that it does not own."*, `In the way: README.md`, nothing created, the README untouched.
- **Fallback:** the card is deterministic (no model); if it does not appear, open Runs -> the stored refusal.

## DEMO 6 - RECOVERY / CANCEL (real, not fabricated)
- **Cancel Run:** start any goal, click **Cancel Run** during Planning. Expected (MEASURED, 3 live cancels): exit in under a second, the card says *Cancelled*, the folder is CLEAN (`kriya runs status`), no records, nothing but `.kriya/` untracked. "Cancelled - recovery required" appears only if Kriya itself reports the folder not clean (unit-tested, never produced by a live cancel).
- **A real failure to explain:** Runs -> open Run 299efed7 (Java `shout`: five attempts, ended `requirements_unresolved` - Kriya refused to call a wrong "upper case" result a success). The plain-language failure card comes first; **View Kriya Log** is the developer-depth action. Replay is labelled *Replay of Run 299efed7 - read from stored evidence, nothing is re-executed*.

## Replay fallback: honest stored runs
| Run | What it shows | State directory |
|---|---|---|
| 1ee786b0 | Greenfield greet.py + tests, success, 100 s | `~/kriya-walkthrough/state` |
| 4fa0397c | Greenfield hello world with the runtime-verification approval, success, 103 s | `~/kriya-walkthrough/state` |
| 76123f00 | Brownfield percent, success, 117 s | `~/kriya-walkthrough/state` |
| bce4bee6 | Java farewell, success, 161 s | `~/kriya-walkthrough/state` |
| 299efed7 | Java shout, honest failure (`requirements_unresolved`), 622 s | `~/kriya-walkthrough/state` |
| two-goal-1 (2 runs) | Two-goal continuity, both success | `~/kriya-evidence/product-72h-env/two-goal-1/state` |
Open Runs -> the run: the result is labelled *Replay of Run <id>*; opening it re-executes nothing. `launch.sh --reset` deletes `~/kriya-walkthrough/state`: copy it first (`cp -R ~/kriya-walkthrough/state ~/kriya-evidence/demo-replay-state`) if you want these to survive a reset.

## STRETCH - Confidence pack (PREPARED ONLY, nothing executed)
Not started: Apache Ignite + Spring XML, Qpid/JMS + custom protocol, Graphify repair. What is KNOWN: the skills `ignite-java17`, `qpid`, `binary-wire-protocol`, `activemq-artemis` ship in `skills/`; the historical goals live in `docs/assurance/` and `handover/` (grep `ignite_qpid_protocol`). What is UNKNOWN and must be decided with the owner before a campaign: the exact repository identities and commits, the goals' final wording, the expected files and preservation contracts, the build/test/runtime commands, the reset procedure and the acceptance rubric. Preparing those from memory would be invention; this section is deliberately a list of open inputs, not a plan.
