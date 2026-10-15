# Live Greenfield Demo 00 - SUCCESS (owner run, 2026-10-15)

Run `20261015T200748-08db1057` (trace row `43c4c2a0`), packaged app built from `ca6f17f`, real local model (qwen3-coder:30b), workspace `~/delete/2/HelloWorld`.
Owner-reported result page: Run completed / Run succeeded, 3 files created, Build passed, 1 / 1 tests passed, application ran (`Hello from Kriya`), commit `COMMITTED`.

Preserved here (copied, not modified; the owner's folder and `~/.kriya` state are untouched):
- `attempt-evidence/` - the sealed attempt-evidence store (records.jsonl, blobs, seal) of the run.
- `evidence-records.kup.json` - `kriya evidence records --json --kup-version 1`: the records exactly as the GUI receives them (characterization fixture for the Activity / verification presentation tests).
- `trace-row.json` - the trace row (prompt text omitted), including `gate_outcomes` with the executed steps.
- `application-outcome.json` - `kriya.kup.application_outcome.application_outcome(gate_outcomes)`.
- `launch-status.kup.json` - `kriya traces --json --launch-status` (approval history, decisions, commit).
- `workspace-kriya/control/` - the run record, launch records and commit transaction.
- `project/` - the committed project files; `kriya.run.log` - the run log.

Facts used by the presentation work (MEASURED from these files):
- Eleven gate records: validator `gate.result` x7 (pom_validate, compile, tests, compile, runtime_verification, tests ...) and workflow `mirror.gate_outcome` x5 (compile, targeted_test, run_verification, goal_spec_compliance, regression_test).
- Pairs (same wall-clock ms): 57/58 compile, 59/60 tests, 78/79 tests; 65/69 runtime. seq 64 is the compile prerequisite of the runtime check (it has no outcome of its own).
- `run_verification` is graded by an LLM (`deterministic_result: null`, `runtime_disposition.authority: llm`); the process facts (`exit_code 0`, `timed_out false`) are on the validator record.
- The executed step's `stdout` is Maven's whole stdout (722 chars); the program's own line (`Hello from Kriya`) is inside it with no structural separation.
- The requirement ledger: two requirements (REQ-1, REQ-2); the verifier verdicts are MODEL_CLAIMED -> `unverified`; no `original_requirements` gate exists.
