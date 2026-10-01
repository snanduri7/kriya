# D3 / D2B evidence (KNOW-A 2026-10-01 run 20261001T141456-fe3a1c9d)

- `d3_reproducer.py`: model-free D3 reproducer (Kriya's own worktree reset + compile gate + runtime gate, real Maven).
- `d3_prefix_reproducer.txt`: its output at d36e3eb, before the fix (unchanged since captured). Its
  `prerequisite_build` field was a probe heuristic that could only ever print false; the reproducer was
  later changed to print the validator's own `runtime_prerequisite` field instead (post-fix file).
- `d3_postfix_reproducer.txt`: the same reproducer after the D3 fix.
- `d2b_reproducer.py`: model-free D2B reproducer (real OCI containment, fresh Maven cache, the candidate's main()
  logs every execution with the JVM proxy it sees and a registry probe).
- `d2b_prefix_reproducer.txt`: before the fix - 2 candidate executions, the first inside the acquisition with
  `https.proxyHost=172.19.0.2` and registry `HTTP 200`.
- `d2b_postfix_reproducer.txt`: after the fix - 1 execution, `https.proxyHost=null`, no network.
- `mutate_version.py` (10/10), `mutate_d3.py` (6/6; first pass 5/6 - the redundant stack guard was removed and
  replaced by a late-pom refresh with its own test), `mutate_d2b.py` (8/8; first pass 7/8 - the profile mount
  test was added).
- `full_suite_first_run_tail.txt`: first full run at the D3/D2B working tree: 1 failure, my own
  KRIYA-VERSION-001 defect (fixed in 413368b). `full_suite_bae131e_tail.txt`: from scratch at bae131e,
  7943 passed / 0 failed / 0 skipped; ResourceWarning kinds unchanged from the earlier baseline (6 = 6, none new).
- `doctor_v4_bae131e.json`: doctor at bae131e run CONCURRENTLY with the full suite - `containment.oci_smoke`
  FAIL because its leak check saw a test's `kriya-oci-*` container (measurement artifact).
  `doctor_v4_bae131e_serial.json`: the same, run alone - production_ready=true, oci_smoke PASS.
