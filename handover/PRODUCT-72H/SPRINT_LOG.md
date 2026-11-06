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
