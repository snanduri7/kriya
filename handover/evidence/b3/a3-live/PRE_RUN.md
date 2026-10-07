# B3 A3 live run (owner-authorized, exactly one run): pre-run binding, committed before any model call

- Kriya: fresh non-editable install of the certified B3 revision `5ab9d69` (`kriya_version.json`, dirty false).
- Workspace: fresh clone of commons-lang at the frozen base `4ee346e59eecccdaefbdd74ac53698da4a1f348a`, never applied;
  the same Maven dependency seed the post-P3 preparation used, keyed to this workspace.
- Config: v5 production operator config, only `paths.*` changed (`config.diff`). SEC-009: owner step before the run.
- Goal: byte-identical to the frozen A3 goal (`goal.txt`).
- Acceptance: the owner-approved suite, byte-identical (sha256 a40b975f..., 4 cases: below min -> min, above max ->
  max, otherwise value, min > max -> IllegalArgumentException).
- B3 approval: the owner-approved A3 approval (sha256 49c05c59...; only `accept_suite_as_sufficient` flipped from the
  template). Both files outside the workspace, bound by `--acceptance` / `--acceptance-approval` before any model call;
  dry bind with the certified install: REQ-1 GENERAL, 4 identities, approval covers REQ-1.
- Only legitimate positive path: all 4 approved cases pass + approval matches + C0 regression + mutation scope + gates
  -> REQ-1 HUMAN_ACCEPTED. Stop conditions as in the authorization.
