# Demo V2 owner runbook

Order: **A** (act: a normal coding run, live local model) -> **B** (learn / reuse: acquired knowledge, offline) -> **C** (prove / recover: a controlled repair).
Everything below is copy/paste. You never need to open an evidence file unless something fails (then: `handover/DEMO-V2/FINDINGS.md`).
Live-model runs are **stochastic**: a run can fail or take more attempts than the figures below. A failed run is a result to keep, not a reason to rerun until it passes.

## A. Prerequisites
- macOS, this checkout on branch `demo/v2`, `.venv` built. Ollama running with `qwen3-coder:30b`, `qwen3.6:35b-a3b-q4_K_M` and `nomic-embed-text` (checked for you, no model is called).
- Java 17+ and the offline Maven repository for scenario A (checked by `prepare_demo.sh --check-build`). Docker is NOT needed (only for the optional live acquisition in E).
- The desktop app built from this checkout: `cd ui/standalone && npm run package:mac` (done for HEAD; `launch_gui.sh` refuses an app older than the newest UI change).

```bash
export REPO=~/WorkingDirectory/AI/ClaudeCode/Kriya-main-demo
cd "$REPO" && source .venv/bin/activate          # puts `kriya` on PATH
export W=~/kriya-demo-v2-work                    # the demo work area (outside the repository)
```

## B. Prepare / reset (one command)
```bash
demo-v2/scripts/prepare_demo.sh --scenario all   # resets and prepares A, B and C; starts NOTHING; grants NO approval
demo-v2/scripts/verify_demo.sh --scenario all --phase idle   # the start state: must end "0 failed" for every scenario and for the global services
```
Each scenario then needs one operator step: approving its workspace configuration (Kriya refuses an unapproved one). **Scenario A: you approve it inside the app** (the Project page shows **ACTION REQUIRED** with **Review & Approve**); no terminal command is needed. Scenarios B and C: approve it the same way in the app, or in a terminal as shown in their sections.

## C. Launch the Kriya GUI
One launch per scenario (quit the app with Cmd+Q before the next one):
```bash
demo-v2/scripts/launch_gui.sh --scenario 01     # or 02, or 03
```
In the app: **Open Folder**, press Cmd+Shift+G in the dialog and paste (the workspace path is already on your clipboard), Enter. The **Project** page opens by itself: type the goal, press **Start Run** (or ⌘ Enter), and the page becomes the run. The left list is Project, Runs, Skills, Tools, Settings (keys 1 to 5); while a run is active every other page shows a strip at the top with **Return to Run**, or **ACTION REQUIRED** with **Review** when Kriya needs you.

## D. Scenario A - normal live coding run (Spring Boot bug fix)
```bash
demo-v2/scripts/verify_demo.sh --scenario 01 --phase idle         # the start state: 0 failed, and the configuration is NOT yet approved (you approve it in the app)
demo-v2/scripts/launch_gui.sh --scenario 01
pbcopy < demo-v2/scenarios/01-normal-coding/goal.md               # the goal
```
In the app: **Open Folder** -> the **Project** page shows **ACTION REQUIRED - Configuration approval required** (the goal box is already there) -> paste the goal -> **Review & Approve** -> read the protected settings (names only; values are never shown) -> **Approve Configuration** -> the page re-checks and turns **Ready**, your goal still in the box, **Start Run** now on -> **Start Run** -> the page names what Kriya is doing (Planning, Editing, Validating) -> when **ACTION REQUIRED** appears again, read the file, `+1 -0`, the validation and the reviewer text (the diff text itself is hidden by Kriya's default content-visibility policy), then **Approve & Continue** (a confirmation follows) -> the page says **Run completed** with the changed file and the recorded validation; **View Evidence** opens the evidence.
The configuration approval is the same approval `kriya authority approve` records (stored outside the workspace, bound to exactly the settings you reviewed; changing any of them asks again). To start over, run `prepare_demo.sh --scenario 01` again: it revokes the approval.
```bash
demo-v2/scripts/verify_demo.sh --scenario 01 --phase pre          # optional, after approving in the app: 0 failed
demo-v2/scripts/verify_demo.sh --scenario 01 --phase post         # after the run: independent check, no model
```

## E. Scenario B - preserved acquired knowledge -> Skills page -> offline reuse
**The skill material is a PRESERVED GOVERNED ACQUISITION SNAPSHOT**: two skills (`tree-sitter`, `tree-sitter-python`) acquired on 2026-10-14 by a governed live lookup (every prompt approved and recorded) in an earlier run. They are **not** acquired during your run. Live acquisition has varied between runs, which is why the demo uses a preserved snapshot.
```bash
( cd $W/scenario-02/workspace && kriya authority approve )        # operator step: answer y
demo-v2/scripts/verify_demo.sh --scenario 02 --phase pre          # snapshot digest, public lookup OFF, no search container, model listed: 0 failed
demo-v2/scripts/launch_gui.sh --scenario 02
```
1. In the app: Open Folder -> click **Skills** (third item in the left list, or key 3). You see both skills, `unverified`, rules 8 and 4, and for each skill its provenance: source `live_lookup:https://...` on every rule, with the date. The GUI is read-only and does not show rule text.
2. Show the goal and that it contains no API answer: `cat demo-v2/scenarios/02-knowledge-skill/goal.md` (it names the task, the library and its pinned versions, nothing about how to call it). Show that public lookup is off: `grep -E -A1 "web_lookup_enabled|^search:" $W/scenario-02/workspace/kriya.yaml` (`false`, and an empty search URL).
3. Run the offline task **in the terminal** (the desktop cannot acknowledge KnowledgeGuard's post-cutoff knowledge risk, so a desktop-started run of this goal stops there by design):
```bash
cd $W/scenario-02/workspace && kriya generate -f "$REPO/demo-v2/scenarios/02-knowledge-skill/goal.md"
```
Answer the three prompts like an operator: **knowledge risk** -> `y`; **Skill Gap ("leave blank to proceed anyway")** -> just press Enter (you supply no reference; no lookup exists anyway; this prompt appears on the first run after a reset); **approve applying these changes** -> `y`. Look for the lines `Loaded engineering skill 'tree-sitter-python' for generation context`: that is the snapshot entering the run.
```bash
demo-v2/scripts/verify_demo.sh --scenario 02 --phase post         # one file changed, 8 passed, skills unchanged
```
**What to say (and not to say).** In the measured no-skill run the same task failed on current API knowledge. In the skill-enabled offline run the acquired rules were supplied and the task completed successfully. That is an observed demonstration of one run each, not a reliability claim.

**OPTIONAL: the live acquisition mechanics** (not needed for the rehearsal; it uses its own run directory and never touches the state above; the content it acquires varies from run to run):
```bash
docker start kriya-searxng-demo
demo-v2/scripts/scenario02_run.sh --run-id owner-live-acquire --mode acquire   # governed prompts are answered from a recorded allowlist and logged
docker stop kriya-searxng-demo
```

## F. Scenario C - controlled deterministic repair
**CONTROLLED DETERMINISTIC MODEL-RESPONSE SCENARIO.** The model's answers come from a stub that replays a fixed script: it is not live model behaviour and must not be presented as such. Kriya's own decisions (gates, baseline comparison, rejection, retry, commit) are real.
```bash
demo-v2/scripts/prepare_demo.sh --scenario 03                     # resets C and STARTS the stub at script position 0
( cd $W/scenario-03/workspace && kriya authority approve )        # operator step: answer y
demo-v2/scripts/verify_demo.sh --scenario 03 --phase pre          # must say 0 failed (includes: stub running at position 0)
demo-v2/scripts/launch_gui.sh --scenario 03
pbcopy < demo-v2/scenarios/03-controlled-repair/goal.md
```
In the app: Open Folder -> paste the goal -> **Start Run**. **Attempt 1** produces a plausible but wrong candidate and Kriya's validation rejects it (a test that passed before now fails); the page then shows **Repairing**. Open **View Details** -> **Details** -> **Attempts**, and the **WHY** panel in the side panel: first failed gate `tests`, the recorded diagnosis, "what Kriya did next". **Attempt 2** is a repair; validation passes; **ACTION REQUIRED** appears: **Approve & Continue**.
```bash
demo-v2/scripts/verify_demo.sh --scenario 03 --phase post
demo-v2/scripts/stub_ctl.sh stop
```

## G. What you should see
- **A:** outcome `success`; exactly one file changed (`DefaultDriverService.java`, one line added); the independent check reports the offline tests 53/53; the run ended after a change review that you approved.
- **B:** the Skills page populated with real data and visible provenance; lookup off; the terminal run ends `Quality Gates: PASSED` and `verify ... post` reports `8 passed`; `outline.py` is the only changed file.
- **C:** two attempts for one unit: attempt 1 rejected by the validation evidence, attempt 2 a repair that passes; `3 passed`; the Attempts view names `scripted-stub` as the model.

## H. Approximate runtime
Measured, not promised. **A:** about 2 to 3.5 minutes (two rehearsals: 2:06 and 2:00 of run time). **B:** the Skills page about a minute; the offline task took 1 attempt (about 1 minute) in one measured run and 4 attempts (about 3.5 minutes) in another, and a run can need more or fail. **C:** about 30 seconds.

## I. Reset between scenarios
```bash
# Cmd+Q the app first, then, for the scenario you just ran (01, 02 or 03):
demo-v2/scripts/prepare_demo.sh --scenario 02
```
`prepare_demo.sh` resets that scenario (`reset_demo.sh` does this part: workspace rebuilt from its fixture, preserved skills snapshot restored for B, Kriya state cleared, operator approval revoked) and places its configuration again. Safe to run twice. Then repeat that scenario's operator step (`kriya authority approve`).

## J. Return the whole demo to a clean state
```bash
# Cmd+Q the app, then:
demo-v2/scripts/prepare_demo.sh --scenario all     # back to exactly the start state of section B: resets A, B and C, stops the stub, any recording proxy and the optional search container, revokes the approvals
demo-v2/scripts/verify_demo.sh --scenario all --phase idle   # confirms it: 0 failed
```
`demo-v2/scripts/reset_demo.sh all` is the same reset without re-placing the configurations (workspaces pristine, nothing running, no approvals; run `prepare_demo.sh` before the next demo). Both are idempotent.
