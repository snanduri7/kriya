# D4: RUNTIME-ENTRYPOINT-OWNERSHIP-001 evidence

Product commit `3615987`. Discovered by KNOW A on demo-runtime-2 (bae131e), whose failed run is kept unchanged at
`~/kriya-live-demo/demo-01-skills/evidence/v4/run-a-20261001T105415Z/` (CLASSIFICATION.md there).

- `d4_precedence_reproducer.py` / `.txt`: model-free. Plain Maven, `mvn -X exec:java -Dexec.mainClass=demo.ExistingB`.
  With a POM exec-maven-plugin `<mainClass>demo.NonexistentA</mainClass>`, the mojo configuration resolves to
  NonexistentA and the JVM reports ClassNotFoundException for it (ExistingB never runs). The no-config control
  runs ExistingB. MEASURED: the POM beats the command-line property.
- `d4_kriya_replay.py` / `.txt`: the same shapes through `PolymorphicValidator.run_app` and
  `acceptance.classify_runtime_entrypoint`. Before D4 both A (candidate POM chose the class) and B (Kriya's command
  chose it) got the same generic infrastructure reason; after D4, A = CANDIDATE_RUNTIME_ENTRYPOINT_INVALID with
  provenance CANDIDATE_BUILD_CONFIG (pom.xml), B = RUNTIME_COMMAND_ENTRYPOINT_INVALID. The valid control passes.
- `mutate_d4.py` / `mutation_d4.txt`: 17/17 mutations killed (rerun at 53dc653, product identical to 3615987).
- `full_suite_3615987_tail.txt`: full parallel suite from scratch at 3615987, default warning filters:
  7965 passed, 0 failed, 0 skipped, 14 warnings (the same 14 fork DeprecationWarnings as before).
- `doctor_v4_3615987.json`: `doctor --production` with the unchanged v4 config (sha256 474346b7...), run serially
  from the KNOW A workspace (which holds the SEC-009 approval): production_ready=true. WARNs: toolchain.required,
  models.role_independence, semantic.precision_boundary (as at bae131e) and persistence.traces /
  runtime.fixed_guarantees, both from the dev checkout's legacy `logs/traces.db` (editable install only; the same
  statuses as `../demo-defects-d3-d2b/doctor_v4_bae131e_serial.json`).
