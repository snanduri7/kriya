# PRD-005 pre-change algorithm audit

Existing guarantees at the PRD-004 verified base:

- Materialize the iterable and reject exact duplicate target strings.
- Validate every expected base content hash before the first source write.
- Recheck each base immediately before its own mutation.
- Use same-directory `os.replace()` for individual file writes.
- Snapshot a target immediately before its mutation and roll back targets already appended to the committed list when a later controlled failure occurs.

Gaps closed by PRD-005:

- Canonical aliases, workspace escapes, base existence identity, invalid create/delete shapes, and directories were not fully preflighted.
- Snapshots and replacement files were prepared during mutation rather than before it.
- `os.replace()` used a newly created temp file mode, losing an existing target's permissions.
- A wrapper/filesystem fault raised immediately after a successful replace/unlink but before `committed.append()` left the changed target outside rollback tracking; the reproduction proves this partial state.
- Rollback success/failure had no durable intent/result record. SIGKILL could leave a mixed source tree with no machine-readable uncertainty signal, and resume did not reject it.
- Directory/file metadata was not fsynced around the commit boundary.
