# product/demo-72h sprint log (started 2026-11-06, base = snapshot/pre-72h-product-sprint-2026-11-06 = 456ec40)

Evidence labels: MEASURED / TRACED / INFERRED / CONFIRMED / UNKNOWN. Evidence outside the repo: `~/kriya-evidence/product-72h-env/`.

## 1. OI-93 (done, a9c5620)
- Classifier fix at the one owner (`goal_requires_runtime_behavior`), terminal invariant pinned independently of the Planner. Details in the commit and registry row `WAVE8-OI93-RUNTIME-INTENT-FALSE-SUCCESS-001` (CLOSED).
- ONE real scenario (live qwen3-coder:30b, contained OCI gates), goal "Create hello.py. It prints hello world when executed. Include a pytest test for it.":
  runtime intent detected -> `Quality Gates: Running application_runtime verification` -> `Runtime verification PASSED` (output contained 'hello world', exit 0) -> `quality_gates_passed: true`, run 20261106T204958-b6ca79b4 (`oi93-live-with-test/`). MEASURED.
- Before the fix the same wording reached success with the application never executed (MEASURED at d7afeb0, `~/kriya-evidence/wave8-final-suite-d7afeb0/characterization/oi93-probe.output`).
- FINDING (MEASURED, 2/2): without a test in the goal ("Create hello.py. It prints hello world when executed.") the Planner emits a runtime-only verification (`application_runtime`, no test step); the contained, network-denied project-venv bootstrap then fails
  (`python3 -m venv` -> `ensurepip --upgrade --default-pip` exit 1 inside the container) and the run ends fail-closed with PYTHON_ENVIRONMENT_UNAVAILABLE (honest: not success, "not a code failure"). The same goal with a test step, and the legacy wording "Run the application and confirm ...",
  pass on this tree and on the untouched baseline. Not caused by OI-93 (the older wording that always forced runtime reaches the same plan shape only when the Planner omits the test step) - INFERRED, not isolated. Demo guidance: include a test in Python greenfield goals, or demo Java/Maven. Registry: `WAVE8-PY-RUNTIME-ONLY-VENV-BOOTSTRAP-001` (P2, OPEN).

## 2. Verified Project Memory (04a815b backend; this commit: preflight block, UI card, capability strip)
Design (owner-approved): a PROJECTION of verified run evidence onto the repository as it is now (`kriya/control/project_memory.py`, STATE); the stored record is a cache, never higher
authority than the repository bytes. Facts: language, build system, Java release, modules, verification gates passed; each carries the SHA-256 of the build file it was read from.
Rules (all pinned in tests/test_project_memory.py, 31 tests, 8/8 mutants killed): verified only from a run that ended in success WITH a passing compile gate; a failed run learns nothing and its
candidates are counted as REJECTED; a changed / missing / swapped-for-symlink evidence file makes the fact STALE with a reason code and it is never reused; a credential-shaped value is rejected,
never stored; no command is ever a fact (a remembered command would be an execution hint; none exists); repository-scoped (a foreign or damaged store contributes nothing); reuse = reference text
into `reference_context` (fenced as untrusted, never joined to the goal). Reached through `ProjectMemoryService` / `MemoryPort` (application + client contract); `kriya generate` loads and records.
Surfaces: `execution.preflight` gains additive `memory` (ProjectMemory) and `health` (HealthRow[]) read-side blocks (schema + generated TS/Java + goldens: INSERTIONS ONLY); UI: `ProjectMemoryCard`
("Why does Kriya know this?", Reused/Learned/Stale/Rejected) and `CapabilityStrip` on the Project page. Both blocks are host-side reads, not part of the launch record's resolution.

REAL two-run proof (live qwen3-coder:30b, contained gates; `~/kriya-evidence/product-72h-env/memory-proof/`), a Python repo with requirements.txt:
- run 1: `Verified Project Memory: learned 3, reused 0, rejected 0` (Language Python, Build system pip, Verification that passed = compile, targeted test, regression test).
- run 2 (a different task): `Verified Project Memory loaded: 3 fact(s) re-checked against the current files.` then `learned 3, reused 3` (facts confirmed 2x, reused 1x each). MEASURED.
- requirements.txt edited: all 3 facts become STALE / EVIDENCE_CHANGED and the projection reuses nothing (counts: current 0, stale 3). MEASURED.

## 3. Findings worth knowing before a demo (MEASURED / TRACED)
- Containment is MANDATORY with no operator opt-out: `autonomy.contained_execution_required: false` is refused at configuration load (ContainmentOptOutError). The Security row states this invariant and a test pins the refusal.
- LSP: `jdtls` has no contained mode and `JdtlsClient.start()` always refuses (kriya/tools/lsp.py); under mandatory containment the language server is never used. The honest "LSP" claim for the demo is
  Kriya's structural code intelligence (symbols, references, implementations; `kriya/code_intel`), not jdtls. The health strip says exactly this (JDTLS_NOT_CONTAINABLE / STRUCTURAL_INDEX).
- Pre-existing at the untouched baseline (identical failure with the product changes stashed): `ui/standalone/test/catalog_real.test.ts` skills.list real-descriptor test.
- Python greenfield without a test step (registry WAVE8-PY-RUNTIME-ONLY-VENV-BOOTSTRAP-001, P2): see section 1.

## 4. Customer-demo continuation (2026-11-06, after the checkpoint was accepted)
Done and committed on product/demo-72h (each with tests; UI: shared suite green, standalone failing only the baseline `catalog_real`):
- **Python venv bootstrap (WAVE8-PY-RUNTIME-ONLY-VENV-BOOTSTRAP-001, P3 OPEN mitigated).** Re-measured: the runtime-only goal that failed twice PASSED the third time with no change (docker-argv shim run) and the identical contained creation
  (same image, flags, nested mounts) succeeded 12/12 on a quiet host -> NOT an ordering defect (the runtime gate already creates the venv). Transient container/mount faults; creation now retries once from nothing and fails closed with both errors (ffcbeba).
- **Security card from REAL refusals** (kriya/policy/security_demonstration.py -> preflight `security`): 6 of 8 checks BLOCKED with the owner's own reason codes/rules (outside write, symlink escape, control state, credential-named file, hard-link read, outside read), 1 ALLOWED control, 1 REDACTED; no path/content/secret carried.
- **Before/After** on the run Overview (request, delivered files +/- lines, Build/Tests/Runtime/Safety rows, attempts) - only what the evidence recorded.
- **Per-run capability states** (USED/ACTIVE/NOT REQUIRED/UNAVAILABLE/not recorded), each naming its evidence; shown only for verified sealed evidence.
- **WHY:** backend-declared gate purpose on gate.result (GATE-RESULT-PURPOSE-LABEL-001 CLOSED); a failure on the ORIGINAL code (named-test base run) is the baseline, not "first failed gate".
- **Configuration overview** (read-only) on the Settings page.
- **JDTLS feasibility (time-boxed trace, STOP):** `JdtlsClient.start()` always refuses and its process machinery was deleted (S8-ORPHAN-HOST-CODE-001: "a contained jdtls would be a new feature, not a revival"); a contained jdtls needs a new pinned image
  (the daemon never pulls), a persistent-stdio profile, a new JSON-RPC client and a network-denied project build import. That is a new containment subsystem: NOT attempted. The product says `Code intelligence - ACTIVE/USED` and `Java language server - UNAVAILABLE (JDTLS_NOT_CONTAINABLE)`.
- **Real Electron walkthrough** (non-run surfaces; the live run waits for the in-flight suite, whose Docker leak checks a concurrent Kriya run would corrupt): the actual Electron app against this tree's backend, state = copy of the memory-proof state, workspace = the real proof repo. Screenshots + DOM results:
  `~/kriya-evidence/product-72h-env/electron-walkthrough-1/` (01-home ... 13-tools, results.json). MEASURED: Home -> Open Folder -> Project: capability strip (Local model "Answering; configured models are served", Code intelligence Available, Language server Unavailable,
  Skills 4 (1 unverified), Tools 0 MCP, Security/Private state Available, Outward lookup Not required, Verify, Recovery, Learning "0 current - 3 stale"), Verified Project Memory card with the three STALE facts (requirements.txt changed) and "Why does Kriya know this?", the Security card
  (6 of 8 blocked); Runs -> "Check for history again" -> 2 real runs -> Overview with the per-run strip ("7 requests answered", "2 files located by the structural index", "3 of 3 recorded checks passed") and the Before/After block (2 files changed +11 -3, Build PASS, Tests PASS 3/3).
- **Defects the real walkthrough exposed (fixed):** (1) a COPIED workspace is, correctly, a different repository: memory said STORE_FOREIGN_REPOSITORY and the run state said RECOVERY_REQUIRED (ownership is bound to the path) - demos must open the real folder, never a copy;
  (2) the per-run Learning cell/memory line were missing for runs that learned and reused facts: three identifiers name one run (trace id, control/evidence id, workflow result id) and memory keyed the wrong one - entries now carry evidence_run_id (f140eb4). Needs a fresh live run to show in the app (the proof's older entries predate the field).
- Known visible limitation (existing OI-91): a CLI-recorded run appears only after "Check for history again"; runs STARTED from the app refresh automatically.
- **NOT done yet (needs the quiet machine or more time):** live run from the Electron app (Start Run -> live progress -> result), Java/Maven memory proof, "Replay of Run <id>" label, outward-lookup exercise (trace: disabled by default; `search.base_url` is empty, so no safe production path to demonstrate without configuring a search endpoint), recovery visibility beyond the Attempts/Timeline modules and the per-run Recovery cell.
