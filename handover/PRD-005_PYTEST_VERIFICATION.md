# PRD-005 Pytest Verification

## Verdict
PYTEST_VERIFIED

## Source identity
- Branch: `codex/fix-demo1-attribution`
- Coding revision: `1d7c94e`
- Live-model verification: NOT REQUIRED

## Independent full-suite result
The user independently ran the full non-live project suite in the target checkout and reported:

```text
4683 passed, 6 deselected, 141 warnings in 896.52s (0:14:56)
```

The JUnit report was generated at `handover/evidence/PRD-005/user-full.xml`. There were no failures or skipped tests. The six deselections are live-model cases excluded by the non-live marker expression. Reported warnings are collection warnings and existing un-awaited AsyncMock/fork deprecation runtime warnings; none changed the pytest verdict.

## Production acceptance
PRD-005 acceptance is verified: controlled mixed-operation failures roll back, revision conflicts mutate nothing, SIGKILL leaves detectable uncertain evidence, and enforce/resume refuses silent continuation from uncertain commit state.
