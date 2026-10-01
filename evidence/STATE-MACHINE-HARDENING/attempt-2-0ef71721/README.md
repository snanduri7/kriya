# Attempt 2 - candidate 0ef71721…, revision 4240d69, Ollama 0.34.4

- Full pytest 7286/0 (0 skips), state-machine tier 582/582 (110.7 s), ruff/pylint zero.
- Production canary: PASS (both runs, every check) - `canary/`.
- Live 11-case matrix: **11/11, CERTIFIED** - `../matrix/trial-20260929T040507Z/` (kept in place).
- Its report exposed STATE-ATTEMPT-METRICS-002 (own bug in 4240d69: a first-pass success reported "Retries 1/0"),
  a reporting-only defect fixed in f4bb39e; the final hardening revision is f4bb39e.
