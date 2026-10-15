# Runtime-approval screenshots (Demo V2, 2026-10-15)

Produced by `demo-v2/scripts/runtime_approval_smoke.sh` against the evidence-packaged app with the product's CONTROLLED workflow body (no model, no live run, scratch project and HOME).
Real: New Project, Project Setup, configuration approval, the launch, the change-approval path (ONE click), the runtime-command approval path, the result screens.
Controlled: the files, test counts and program output are scripted test data - NOT a claim about a real run.

Known limits, stated plainly:
- **08 (native Run Commands confirmation) is not in this set.** It is an operating-system sheet and cannot appear in an in-page screenshot. Its scripted answer (`approve` / `cancel`) and effects are captured (09, 17).
  The sheet's text is pinned by `ui/standalone/test/runtime_command_host.test.ts`; a human must see the real sheet once.
- The controlled body applies nothing to the project folder, so 12 shows "Kriya did not record that the change was applied" (the "Application created successfully" variant needs a COMMITTED result and is pinned by
  `ui/shared/src/cockpit/shell/runtime_command_review.test.tsx`).
- The controlled body records only a test gate and the runtime step, so Verification shows those lines only.
- 03 is the earliest in-progress state the driver reached (STARTING); 19 is the controlled failure; 18 uses a recorded `MANAGED_SERVICE_APPROVAL_UNSUPPORTED` gate outcome (a controlled stand-in for the real HITL refusal).

Files: 00 launch, 01 project ready, 02 goal entered, 03 starting, 04 source diff, 05 after the ONE source click, 06 tests with a nonzero count (3 / 3) and the overall-goal line, 07 runtime command review,
09 running after the (scripted) native confirmation, 10 Activity drawer, 11 application output, 12 completed overview, 13 verification, 14 Project details, 15 Technical details,
16 runtime command rejected, 17 native Cancel (still pending), 18 managed-service unsupported, 19 general failure. `results-*.json` hold what each step read from the page.
