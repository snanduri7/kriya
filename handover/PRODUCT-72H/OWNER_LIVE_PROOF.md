# Owner-assisted live proof - the flagship runtime-verified run

Status words: FUNCTIONALLY WIRED / OWNER VISUAL REVIEW PENDING. Prepared 2026-11-09 on `product/demo-72h` at `15b543b` (local, nothing pushed or tagged). **Not started**: the run stops at a native macOS confirmation that automation cannot press, and a run left waiting would only time out. Everything else is ready.

## What this proves, and what it does not
It is the live acceptance run for the result-truth correction (`aa7cb9e`, `c3f1318`) and the contained-environment fix (`15b543b`, P1 RUNTIME-CONTAINED-ENVIRONMENT-INTERMITTENT-DOCKER-FAILURES-001). ONE clean run cannot close that P1 (the failure was intermittent and load-dependent); it counts as one acceptance proof. The P1 closes on this run PLUS repeated clean desktop runs, or an isolated cause for the pip-vendored symptom (registry row, SPRINT_LOG 12c).

## Prepared state (already done)
- Preflight passed: model server serves `qwen3-coder:30b` and `nomic-embed-text`, Docker daemon running, tree clean at `15b543b`, cockpit rebuilt from this tree.
- A dedicated proof home exists, separate from your normal walkthrough: `~/kriya-walkthrough-p1proof` (state, user data, empty `projects/` parent, two demo repos). Your real `~/.kriya` state is not used by the app (the app reads the proof home's `state/`).
- Docker had NOT been restarted (restart would be a separate control experiment; "not reproduced after restart" never closes the P1).
- Do not run pytest or any other Docker work while this run is active (the kit warns about it).

## Do exactly this
1. In a terminal: `cd ~/WorkingDirectory/AI/ClaudeCode/Kriya-product-72h && KRIYA_WALKTHROUGH_HOME=$HOME/kriya-walkthrough-p1proof handover/PRODUCT-72H/walkthrough/launch.sh --no-build`
2. In the app: Home -> **New Project...** -> parent folder `~/kriya-walkthrough-p1proof/projects` (Cmd+Shift+G in the folder dialog; the path is on your clipboard) -> name `hello-proof` (a NEW name, never reused).
3. Goal, exactly: `Create hello.py. It prints hello world when executed. Include a pytest test for it.` -> Start Run.
4. Watch the stepper (Planning -> Implementing -> Verifying -> Complete) and open the dock tab **Kriya Log**.
5. When the change approval appears (about 45-70 s in): **Approve & Continue**.
6. **STOP POINT (yours):** the app asks **Continue to Run Approval...**, then a NATIVE confirmation. Read the command it shows. It must be `.kriya/venv/bin/python hello.py` (the effective argv). Approve it.
7. Expected total: about 100-125 s (MEASURED earlier for this goal: 103 s).

## What to verify after approval (tick each; any miss = a finding, do not rerun the same goal)
- [ ] The approval card showed the effective command `.kriya/venv/bin/python hello.py` (not a bare `python`).
- [ ] The stepper moved Planning -> Implementing (approval) -> Verifying -> Complete.
- [ ] Headline reads **Completed — verified** and **3 of 3 requirements verified** (file creation, runtime stdout, test addition).
- [ ] **Observed output** under Verification: command, the printed text `hello world`, exit status 0.
- [ ] **How to run** shows `python3 hello.py` with Copy and Open Terminal Here (host-planned, nothing executed).
- [ ] **Kriya Log** tab shows real application-log lines (model requests, "Developer: generating content", the review banner, "Applied terminally verified sandbox change", "Work unit 'direct': VERIFIED"); paths shown project-relative or with `~`.
- [ ] **Runs list**: the card says **Completed — verified** (not "Succeeded").
- [ ] Run header badge and Overview say the same; Activity closing line says **Completed — verified**.
- [ ] The macOS notification (if enabled) says "Completed — verified: every requirement was verified" (not "success recorded").
- [ ] Open in Finder shows `hello.py` and `test_hello.py`; nothing is committed to Git.

## If it fails
Do not rerun the identical goal. Keep the evidence: the run id is on the result card; copy `~/kriya-walkthrough-p1proof/state` and `~/.kriya/logs/runs/<run id>/` to `~/kriya-evidence/product-72h-env/p1-owner-proof-<date>/`. If the card says the verification environment could not run (PYTHON_ENVIRONMENT_UNAVAILABLE / "could not be verified ... not a finding about your code"), that is the product reporting a verification-environment stop truthfully: record the exact text, check `ls ~/kriya-walkthrough-p1proof/state/gate-views` (leftover directories are named `view-<16 hex><8 hex><6 hex>` and are swept by the next claim), and report it as a P1 data point. The Docker backend logs rotate about every two hours: copy `~/Library/Containers/com.docker.docker/Data/log/host/com.docker.backend.log*` right away.

## After a clean run (a note for the next session, not for you)
Capture the screens (the earlier harness is gone; use your own screenshots), then add one data point to SPRINT_LOG 12c. Repeat the same flagship goal from a NEW project name on two further occasions on different days/loads before downgrading the P1; use the heavy-load condition (`scratchpad/p1_load.sh`-style container IO, see `~/kriya-evidence/product-72h-env/p1-incident-snapshot-1/probes/`) for at least one of them.
