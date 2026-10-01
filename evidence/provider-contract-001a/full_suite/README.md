# Full-suite evidence (PROVIDER-CONTRACT-001A)

Command: `ulimit -n 256; .venv/bin/pytest -q -n 8 --dist loadgroup` (Python 3.14.6, macOS, M1 Max).

| Run | Revision | Result |
|---|---|---|
| After F-1/F-3 | `662feae` | 7868 passed, 2 failed. Both failures were the exact-runtime test stand-in, which reported no served window; fixed in the same commit and green on rerun. |
| **Gate** | **`2e8b09f`** | **7883 passed, 0 failed, 0 skipped, 14 warnings** (`fork()` DeprecationWarnings, as at baseline), 7m58s |
| Strict ResourceWarning comparison | `2e8b09f` vs `2f62430` | 73 vs 71 failing tests under `-W error::ResourceWarning`. The leaked-object kinds are the same; sqlite leaks are 96 vs 110. The failing IDs vary run to run, because garbage collection decides which test pays. The leaks are pre-existing test hygiene (registry `TEST-RESOURCEWARNING-HYGIENE-001`), and there are no new ResourceWarnings. |
