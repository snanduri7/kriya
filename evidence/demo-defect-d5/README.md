# D5: RUNTIME-RESOURCE-GROUNDING-001 evidence

Product commit `4834164`. Discovered by KNOW A on demo-runtime-3 (3615987), whose run is kept unchanged at
`~/kriya-live-demo/demo-01-skills/evidence/v4/run-a-20261001T113537Z/` (CLASSIFICATION.md there).

- `fixtures/live_runtime3_s4_output.txt`: the s4 runtime failure's captured output, extracted verbatim from that run's
  stderr.log. `fixtures/spring_probe_*.txt`: real Spring 5.3.39 runs under `mvn -o -e exec:java` (invalid property,
  relative `file:` resource, a bean constructor that throws, an import of an invalid XML, a missing resource). These
  were measured before any pattern was written; only formats seen here are recognized.
- `d5_reproducer.py`: model-free; replays each output through the production path
  (`_build_quality_gate_failure("run_verification")` -> `attribute_failure` -> the scope conflict's `raw_evidence`).
  - `d5_reproducer_prefix.txt` (at 53dc653 code): all 4 resource cases ground to the loader (IgniteDemoApplication.java /
    App.java); the live owner evidence (first 2000 chars) contains no exception at all.
  - `d5_reproducer_postfix.txt`: each grounds exactly the named resource (ignite-config.xml, bad-config.xml twice,
    imported-bad.xml); the candidate-throws control stays [App.java, Widget.java]; the owner evidence shows the exception.
  - The reproducer's first version checked `handle_attempt_failure`'s source for the new excerpt (a wrapper - wrong
    place) and so under-reported the post-fix evidence; corrected to the module before either recorded file was
    kept. The pre-fix file was regenerated with the corrected reproducer against the stashed pre-fix code: identical.
- `mutate_d5.py` / `mutation_d5.txt`: 16/16 killed. A first run left 5 survivors: two outside-path guards and a
  trailing-summary cut were redundant (exact matching against workspace-relative files can never match `../`; real
  Maven's trailing `[ERROR]` lines name no resource) and were removed; two tests were too weak and were strengthened.
- `full_suite_4834164_tail.txt`: 7995 passed, 0 failed, 0 skipped, 14 warnings (the same fork DeprecationWarnings).
  The D5 tests under `-W error::ResourceWarning`: 30 passed, none emitted.
- `doctor_v4_4834164.json`: v4 config unchanged, run serially from the KNOW A workspace: production_ready=true; WARNs
  as at 3615987 (dev checkout).
