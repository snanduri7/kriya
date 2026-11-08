# Demo flows - FUNCTIONALLY WIRED / OWNER VISUAL REVIEW PENDING (rewritten 2026-11-09 for `product/demo-72h` at `15b543b`)

Every duration, file list and count below was MEASURED in a real run unless it says UNPROVEN or TESTED. The model is local and non-deterministic: **never rerun an identical failed goal hoping it passes** (use the fallback in each demo). Start the app with `handover/PRODUCT-72H/walkthrough/launch.sh` (add `--reset` ONLY before a full pass: it wipes the walkthrough home including the stored runs the Replay fallback uses; copy `~/kriya-walkthrough/state` first, see Demo 7). Do not run pytest or any Docker work while a live run is active. The flagship acceptance run has its own sheet: `OWNER_LIVE_PROOF.md`.

## 0. The words on every result (one table, every surface)
| Backend truth | Words everywhere (card, Runs list, run header, Overview/Replay, Activity, notification) |
|---|---|
| every requirement closed by deterministic evidence | **Completed — verified** (+ "N of N requirements verified") |
| no open requirement, at least one the operator ACCEPTED | **Completed — requirements resolved** (+ "N verified · M accepted by you"); acceptance is never "verified" |
| some requirement has no independent evidence | **Changes applied — verification incomplete** (+ "N of M verified - K unverified", the unresolved listed) |
| a requirement contradicted by evidence | a non-success presentation (a quoted runtime mismatch blocks the apply) |
| a run recorded before the truth event existed | **Completed** (neutral; never "verified") |

Say which one you are showing: it is the product telling the truth, not a failure. Other honest states: *Observed output* (the command, what it printed, exit status, from the recorded run); *How to run* (a command only when Kriya itself ran the application and saw it succeed, otherwise "Kriya did not run this application, so it has no verified run command."); the **Kriya Log** tab is Kriya's real application log, Activity is the summary.

## DEMO 1 - Greenfield creation
- **Starting state:** `launch.sh` -> Home -> New Project... -> parent `~/kriya-walkthrough/projects` (empty), name `greeter-demo` (a NEW name every pass, never reuse a folder).
- **Goal (exact):** `Create greet.py with a function greet(name) that returns "Hello, <name>!" and test_greet.py with two pytest tests for it.`
- **Expected approval:** the change (diff) approval appears ~45-70 s in: **Approve & Continue**. No runtime approval (the goal states no runtime obligation).
- **Expected files (MEASURED):** `greet.py +2 −0`, `test_greet.py +8 −0`; Build passed, 2 / 2 tests passed. Nothing is committed to Git.
- **Expected requirement truth:** *Changes applied — verification incomplete*, 0 of 1 requirements verified (the return value is a placeholder: GENERAL behaviour, no deterministic authority). LIVE on the backend (CLI) 2026-11-08; the desktop wording is TESTED (fixtures) and pending the owner's eyes.
- **Expected runtime evidence:** none; How to run says there is no verified run command.
- **Expected duration (MEASURED, 3 live runs):** 100-122 s. Stepper: Planning -> Implementing (approval) -> Verifying -> Complete.
- **Variant with a verified run command (needs your click; the flagship, see `OWNER_LIVE_PROOF.md`):** goal `Create hello.py. It prints hello world when executed. Include a pytest test for it.` -> after the diff approval: **Continue to Run Approval...**, then a NATIVE confirmation showing `.kriya/venv/bin/python hello.py` -> *Completed — verified*, 3 of 3 verified, Observed output `hello world` exit 0, How to run `python3 hello.py` with Copy and Open Terminal Here. Duration 103 s (stored run 4fa0397c); backend proof `~/kriya-evidence/product-72h-env/closer-live-1`, recipe proof `.../recipe-live-1`.
- **Reset:** a new project name each pass; `launch.sh --reset` for a full reset.
- **Fallback if the model diverges / fails:** do not rerun. Runs -> **Replay of Run 1ee786b0** (same goal, succeeded) and say it is a stored real run (Demo 7).

## DEMO 2 - Second-goal continuity (Kriya sees and edits what it built)
- **Starting state:** directly after Demo 1, same project; click **New Goal** (the box is blank).
- **Goal (exact):** `Change the greet function in greet.py to return "Hi, <name>!" instead, and update its tests to match.`
- **Expected approval:** the diff approval.
- **Expected files (MEASURED, `~/kriya-evidence/product-72h-env/two-goal-1`):** `greet.py +1 −1`, `test_greet.py +2 −2`, NO new file; the folder holds exactly the two files; `greet.py` returns `"Hi, {name}!"`; 2 / 2 tests passed. Before the fix this goal created a substitute `app.py`.
- **Expected requirement truth:** *Changes applied — verification incomplete* (a general behaviour statement) - UNPROVEN live on this tip (the two-goal runs predate the truth event); expected by the same grammar as Demo 1.
- **Expected runtime evidence:** none.
- **Expected duration (MEASURED):** goal 1 2:02, goal 2 1:33 (`two-goal-1`).
- **What to say:** "Kriya shows the planner and the reviewer the project as it is now - including files it created that Git does not track yet - so the second goal edits the same file."
- **Reset:** a new project name. **Fallback:** Replay of the two stored runs: launch with `KRIYA_STATE_DIR=~/kriya-evidence/product-72h-env/two-goal-1/state` and open them in Runs.

## DEMO 3 - Brownfield modification (existing repo, code intelligence)
- **Starting state:** Open Folder -> `~/kriya-walkthrough/brownfield-python` (a calculator repo, one commit). Open the REAL folder, never a copy.
- **Goal (exact):** `Add a function percent(part, whole) to calc.py that returns part as a percentage of whole rounded to one decimal place, and add a pytest test for it.`
- **Expected approval:** the diff approval, then the result.
- **Expected files:** `calc.py` (the function) and a pytest test; the goal names its file, so Code Intelligence finds it.
- **Expected requirement truth:** *Changes applied — verification incomplete*: the test-addition requirement closes on before/after evidence, the formula over parameters stays open (deterministic characterization, `tests/test_requirement_truth_characterization.py`; SPRINT_LOG 11). A goal that carries an exact example line (`percent(1, 8) -> 12.5`) would be legitimately verifiable; that is a different goal and is not run here.
- **Expected runtime evidence:** none (no runtime obligation stated).
- **Expected duration (MEASURED):** 117 s. Project page -> Verified Project Memory: *Learned last run 3* (language Python, build system pip, evidence requirements.txt).
- **Reset:** `launch.sh --reset` restores the repo. **Fallback:** Replay of Run 76123f00 (percent, succeeded).

## DEMO 4 - Verified Project Memory (learn -> reuse -> stale)
- **Starting state:** right after Demo 3 on the same repo (memory starts empty after `--reset`; say plainly that memory facts are language, build system, release, modules and which gates passed - never commands).
- **Goal 2 (exact):** `Add a function multiply(a, b) to calc.py that returns the product of a and b, and add a pytest test for it.` -> *Reused last run 3*. (The first multiply run ended in the now-fixed optional-runtime stop, run 304c8d2e; a rerun on this tree is expected to pass but is UNPROVEN live.)
- **Stale:** in a terminal `echo "# demo" >> ~/kriya-walkthrough/brownfield-python/requirements.txt`, then Project -> the three facts read STALE / EVIDENCE_CHANGED.
- **Expected approval / files / truth:** as Demo 3 (diff approval; `calc.py` + a test; *verification incomplete*). **Duration:** not measured for multiply.
- **Java variant:** `java-maven` (runbook section 5; goals name package and file on purpose). Fallback Replay of Run bce4bee6 (farewell, succeeded, 161 s).
- **Reset:** `launch.sh --reset`. **Fallback:** the stored runs above.

## DEMO 5 - Security refusal (a real refusal, with the reason, no secrets)
- **Starting state:** the Project page -> **Security** card, "What Kriya refuses": live checks of Kriya's real file protections against situations built in a disposable folder (never your project): a typed reason code and rule per check, no secret values.
- **A real refusal in the product:** `mkdir /tmp/not-empty && echo hi > /tmp/not-empty/README.md`; Open Folder -> that folder; goal `Create greet.py with a function greet(name)`.
- **Expected (MEASURED):** refused before any model call with *"Kriya can't start this new project because the folder already contains files that it does not own."*, `In the way: README.md`, nothing created, the README untouched. No approval is asked. Deterministic, no model (so no duration to quote).
- **Reset:** `rm -rf /tmp/not-empty`. **Fallback:** Runs -> the stored refusal.

## DEMO 6 - The real Kriya Log, Build and Tests
- **Starting state:** any live run (Demo 1 is the natural one); the dock tabs are Activity (default), **Kriya Log**, **Build**, **Tests**.
- **Expected (MEASURED live on the two-goal run):** the Kriya Log is the real per-run application log through a read-only, bounded, twice-redacted KUP read: 16 lines during planning, 51 at the approval, 53 while verifying, 80 at completion; real records (model requests, "Developer: generating content for 'greet.py'", the REVIEW banner, "Escalating changes to human approval gate", "Applied terminally verified sandbox change to workspace", "Work unit 'direct': VERIFIED"). Follows only while at the bottom, Jump to latest, level filter, Copy visible lines; **View Kriya Log** on a failed run. Paths are project-relative or `~` (display only). Build: *Build passed*; Tests: *2 / 2 tests passed* (Demo 1).
- **Expected approval / files / truth:** those of the run it is shown on. **Duration:** that run's.
- **Fallback:** open any stored run: its Kriya Log reads the same file from `~/.kriya/logs/runs/<id>/`.

## DEMO 7 - Replay (stored real runs; nothing is re-executed)
| Run | What it shows | State directory |
|---|---|---|
| 1ee786b0 | Greenfield greet.py + tests, success, 100 s | `~/kriya-walkthrough/state` |
| 4fa0397c | Greenfield hello world with the runtime-verification approval, success, 103 s | `~/kriya-walkthrough/state` |
| 76123f00 | Brownfield percent, success, 117 s | `~/kriya-walkthrough/state` |
| bce4bee6 | Java farewell, success, 161 s | `~/kriya-walkthrough/state` |
| 299efed7 | Java shout, honest failure (`requirements_unresolved`: Kriya refused to call a wrong "upper case" result a success), 622 s | `~/kriya-walkthrough/state` |
| two-goal-1 (2 runs) | Two-goal continuity, both success | `~/kriya-evidence/product-72h-env/two-goal-1/state` |

- **Expected wording:** these runs were recorded BEFORE the requirement-truth event existed, so the Runs list and Overview say the neutral **Completed**, never "verified" (the fixtures and the closer-live-1 backend run show the truth words).
- **Expected approval:** none (read-only). **Starting state:** Runs -> the run; the result is labelled *Replay of Run <id> - read from stored evidence, nothing is re-executed*.
- **Reset hazard:** `launch.sh --reset` deletes `~/kriya-walkthrough/state`: copy it first (`cp -R ~/kriya-walkthrough/state ~/kriya-evidence/demo-replay-state`).

## APPENDIX - Recovery / cancel (real, not fabricated)
**Cancel Run:** start any goal, click **Cancel Run** during Planning. MEASURED (3 live cancels): exit in under a second, the card says *Cancelled*, the folder is CLEAN (`kriya runs status`), no records, nothing but `.kriya/` untracked. "Cancelled - recovery required" appears only if Kriya itself reports the folder not clean (unit-tested, never produced live). **A real failure to explain:** Runs -> Run 299efed7; the plain-language failure card comes first; **View Kriya Log** is the developer-depth action.

## STRETCH - Confidence pack: see `CONFIDENCE_PACK_PREP.md` (prepared only, nothing executed)
