# Wave 0 Verification

## Verdict
VERIFIED

The user independently ran the complete non-live suite after PRD-003:

- 4,651 passed, 13 skipped, 6 deselected, 145 warnings in 843.44 seconds.
- The 13 skips were traced to five test modules hard-coding
  `.venv/bin/kriya` while the active installation was `.newvenv`.
- Commit `03569fc` changed those tests to resolve `kriya` beside the active
  Python interpreter. A coding-agent check then collected 142 tests across
  those modules and produced 141 passes plus one Docker-daemon skip inside the
  restricted sandbox.
- The user reran the corrected security suites in the real environment and
  reported that all passed. Given the 142-test collection established by the
  preceding run, the resulting count is 142 passing tests with no environment
  skip; this count is derived from collection plus the user's pass report.
- The user also ran `scripts/verify_release.sh` and reported that the source,
  sdist, wheel, clean dependency installation, `pip check`, and installed CLI
  smoke all passed.
- PRD-003 live verification passed separately: 1 passed in 17.48 seconds.

PRD-001 and PRD-002 require no live-model test. PRD-003's live result and exact
Ollama runtime evidence are recorded in `PRD-003_LIVE_VERIFICATION.md` and the
user's preserved evidence directory.

The Wave 0 release gate is closed. PRD-004 may begin.
