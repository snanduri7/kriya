# PRD-004 Pytest Verification

## Verdict
PYTEST_VERIFIED

## Source identity
- Branch: `codex/fix-demo1-attribution`
- Coding revision: `ee673e0` (including the isolated event-test fixture correction)
- Live-model verification: NOT REQUIRED

## Independent full-suite result
The user independently ran the full non-live project suite in the target checkout and reported:

```text
4671 passed, 6 deselected, 143 warnings in 899.99s (0:14:59)
```

There were no failed or skipped tests. The six deselections are the live-model cases excluded by the non-live marker expression.

## Production acceptance
PRD-004 acceptance is verified: terminal correctness gates operate on the isolated candidate before one real-workspace batch commit, terminal failures do not apply candidate source changes, and post-commit persistence/observability failures do not retroactively change verification truth.
