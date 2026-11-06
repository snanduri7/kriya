# Demo runbook - product/demo-72h (what is VERIFIED here, and what needs the owner's eyes)

Status words: **VERIFIED** = I ran it in this session and saw the result; **WIRED** = covered by automated tests but never clicked by a person; **NOT DONE** = not built.
Nothing below is "visually approved": the final click-through is the owner's.

## What runs live (VERIFIED, real backend + live qwen3-coder:30b + contained gates)
Environment: Ollama up with qwen3-coder:30b / qwen3.6:35b-a3b-q4_K_M / nomic-embed-text; Docker Desktop with the kriya-acq-tools images; this worktree's own `.venv`.

1. **Greenfield, runtime-verified (OI-93).** In an empty git repo (one commit):
   `KRIYA_STATE_DIR=<dir> .venv/bin/kriya generate -y --json "Create hello.py. It prints hello world when executed. Include a pytest test for it."`
   -> `Quality Gates: Running application_runtime verification` -> `Runtime verification PASSED` -> `quality_gates_passed: true`. Without "Include a pytest test" the run fails closed
   (PYTHON_ENVIRONMENT_UNAVAILABLE, registry WAVE8-PY-RUNTIME-ONLY-VENV-BOOTSTRAP-001): for a Python demo ALWAYS ask for a test.
2. **Second task, same repository (Verified Project Memory).** Run 1 prints `Verified Project Memory: learned 3, reused 0, rejected 0.`; run 2 prints
   `Verified Project Memory loaded: 3 fact(s) re-checked against the current files.` then `learned 3, reused 3`. Edit `requirements.txt` (the evidence file) and the three facts are STALE /
   EVIDENCE_CHANGED. Evidence: `~/kriya-evidence/product-72h-env/memory-proof/` (projections after run 1, run 2 and after the edit).
3. **Where the memory shows (WIRED, fixture-tested + backend-tested, not clicked):** Project page -> capability strip (Local model, Code intelligence, Language server, Skills, Tools, Security,
   Private state, Outward lookup, Verify, Recovery, Learning) and the "Verified Project Memory" card (Reused / Learned / Stale / Rejected, "Why does Kriya know this?"); a run's Overview adds
   "Verified Project Memory: Reused N ...". All of it comes from `execution.preflight.{memory,health}`; the renderer computes nothing.

## Launching the cockpit against this tree (NOT verified by me)
`cd ui/standalone && npm run build:cockpit` builds (VERIFIED: build + bundle check pass). Starting the Electron app (`npm run start`, see ui/standalone/README and demo-v2/scripts/launch_gui.sh) and pointing its
`kriyaExecutable` setting at `<this worktree>/.venv/bin/kriya` was not done here. The state directory the app reads must be the one the run writes (`STATE_DIRECTORY_NOT_OBSERVABLE` otherwise).

## Honest limits (read before presenting)
- **Language server:** jdtls has no contained mode and is never started; the strip says UNAVAILABLE / JDTLS_NOT_CONTAINABLE. The code-understanding claim is the structural index (symbols, references), not an LSP.
- **Containment is mandatory with no opt-out** (the configuration refuses any other value) - a true, demonstrable security statement.
- **Outward lookup** is off by default (LOOKUP_DISABLED). Nothing here exercises or demonstrates a lookup; do not claim a live lookup without enabling it through the operator configuration and running it.
- **Memory facts** are language, build system, Java release, modules and which verification gates passed - no commands. A Java/Maven repo is covered by unit tests (pom.xml evidence), not by a live Maven run in this session.
- **NOT DONE:** per-run USED/ACTIVE capability states, the security-refusal gallery, recovery diagram beyond the existing Attempts/Timeline modules, before/after summary, "Replay of Run <id>" mode
  (a recorded run's state directory can be opened read-only by pointing the cockpit at it, but no "Replay" label exists), WHY gate-purpose label (backend field missing: GATE-RESULT-PURPOSE-LABEL-001).
- Pre-existing and unrelated: `ui/standalone/test/catalog_real.test.ts` skills.list (fails identically on the untouched baseline).
- Scenario-04 (OI-125) still needs the owner, the desktop app and the live model on the final tip.
