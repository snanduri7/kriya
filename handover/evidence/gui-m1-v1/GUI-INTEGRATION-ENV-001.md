# GUI-INTEGRATION-ENV-001 — serializer fixtures hard-coded `../.newvenv` (FIXED in d842c27)

## Observation (MEASURED)

In the integration worktree `npm run fixtures:serializer:check` failed before any check ran:
`sh: ../.newvenv/bin/python: No such file or directory`. `.newvenv` is a virtualenv that exists only in the original
GUI checkout; the npm scripts named it by directory.

## Root cause (CONFIRMED)

Discriminating check: the same three committed scripts (`fixtures/serializer_{events,gates,evidence}.py --check`) run
with a real interpreter (`.venv/bin/python`) all passed against the integrated code. The hard-coded interpreter path
was the only cause; no contract or Kriya code was at fault.

## Fix

- `ui/scripts/serializer-fixtures.mjs`: runs the three scripts with `$KRIYA_PYTHON`, else `python3` from the active
  environment; stops with exit 2 and a clear message before any script when that interpreter cannot `import kriya`.
- `ui/package.json`: `fixtures:serializer[:check]` call the runner; new `test:scripts` (`node --test
  "scripts/*.test.mjs"`) added to `npm run check`.
- Three fixture docstrings updated to the new usage. No Kriya code changed.

## Verification

- Original symptom re-measured: `KRIYA_PYTHON=.venv/bin/python npm run fixtures:serializer:check` → all three current,
  exit 0; activated venv without KRIYA_PYTHON → exit 0; `KRIYA_PYTHON=/usr/bin/python3` (no kriya) → exit 2.
- `scripts/serializer-fixtures.test.mjs`: 3/3. Negative controls, each failing the test: (A) the old `.newvenv` npm
  script restored, (B) runner ignoring KRIYA_PYTHON, (C) runner without the import-kriya probe.
- Own defect found while doing this: the first `test:scripts` used `node --test scripts/`, which Node 26 loads as a
  module path (MODULE_NOT_FOUND) — caught because the unmutated run failed; corrected to an explicit glob before the
  negative controls were trusted.
- Full `npm run check` PASS with the fix.
