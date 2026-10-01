# D8: TERMINAL-TOOLCHAIN-REAUTHORIZATION-001 evidence

Product commit: the D8 commit on `milestone-decomposition`. Found by KNOW B on demo-runtime-6; the run is kept unchanged at
`~/kriya-live-demo/demo-01-skills/evidence/v4/run-b-20261001T133525Z/` (CLASSIFICATION.md).

- `d8_reproducer.py`: model-free; the real `close_requirements_with_named_tests` under the production containment
  setting, with a greenfield workspace (default Java 21) and a candidate pom.xml declaring Java 17. Validator
  construction is counted; test execution is stubbed.
  - `d8_reproducer_prefix.txt`: no-named-test (KNOW B) and one-named-test shapes both construct a validator and raise
    `TOOLCHAIN_REQUIREMENT_CONFLICT`; the named test never runs.
  - `d8_reproducer_postfix.txt`: no named tests means 0 validators, no exception; one named test means 1 validator
    under the plan's authority, the test runs, the requirement closes.
- `mutate_d8.py` / `mutation_d8.txt`: 7/7 killed. `pre-scan-ignores-verdicts` survived the first run, so a test for
  a named test whose requirement is already satisfied was added.
- `full_suite_tail.txt`: 8055 passed, 0 failed, 0 skipped, 14 warnings. D8 tests under `-W error::ResourceWarning`:
  13 passed.
- `doctor_v4.json`: v4 config unchanged, run serially: production_ready=true.
