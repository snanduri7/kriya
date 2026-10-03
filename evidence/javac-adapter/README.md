# Capability Adapters R1 - javac slice: gate evidence

Branch feature/javac-build-adapter-r1 (local, not pushed), base origin/main 0bea22f.
Code: f80a044 (adapter) + 2078811 (mutation-survivor test).

- Mutations (`mutations.json`, 13 cases): first run 12/13 - the egress guard of the resolver's external lookup
  survived (every test used web_lookup_enabled False); test added in 2078811; re-run 13/13 killed.
- Full parallel suite at 2078811: 8389 passed, 0 failed (`full_suite_tail.txt`; 8376 on the base + 13 new tests).
- ruff (tracked files) clean; `pylint kriya plugins/core_tools tests` exit 0.
- javac smoke (`javac_smoke.txt`, real /usr/bin/javac, worktree code): plain Java workspace -> stack java; valid
  two-file compile succeeds into build/; a broken file fails with "Java compilation failed:" and javac's
  "cannot find symbol"; the test gate reports "No Java test config found"; gate output root = build/.
- doctor --production (Spring XML bench workspace, v5-derived config): 34 status rows identical to the 0bea22f
  baseline taken under the same worktree install-dir condition (`doctor_statuses.txt`; exit 1 = the pre-existing
  PRODUCTION_READY=false).
