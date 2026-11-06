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
