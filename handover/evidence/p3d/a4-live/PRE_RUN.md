# A4 live post-P3-D (owner-authorized, exactly one run): pre-invocation verification

Verified before invoking (MEASURED, 2026-10-06), no model call:
- Kriya `bcb0c1d` non-editable install (`venv-p3d`, dirty false) - the A3 install, unchanged.
- Workspace `ws/p3v-a4-java-cli`: HEAD d95484f57dfa4b99ac9069bb3f44df680306e8e9 (frozen base), `git status
  --ignored` empty (no tracked, untracked or ignored entries; never run).
- Goal `goals/p3v-a4-java-cli.txt` (404 bytes, trailing newline), unedited: sha256(bytes + NUL) = f6c762d8... ==
  approval goal_sha256. Passed by `-f` with an ABSOLUTE path.
- Acceptance: byte copy of the owner-reviewed `handover/evidence/b3/a345/A4_KriyaAcceptanceTest.java` to
  `acceptance/p3v-a4-java-cli/KriyaAcceptanceTest.java`, sha256 84fae0a6... == approval acceptance_sha256.
- B3 approval: byte copy of `approvals/A4_approval.json` to `acceptance/p3v-a4-java-cli/approval.json`, sha256
  2ae4a2e9..., not regenerated or edited (accept_suite_as_sufficient true, base d95484f5, 5 expected cases).
- Dry bind with the bcb0c1d install (scratch state root): REQ-1 + REQ-2 derived; acceptance 5 cases for REQ-1, java;
  runner contract 36363c7d... == approval runner_contract_sha256; approval loads, covers REQ-1, base matches HEAD.
- Config `config/p3v-a4-java-cli.yaml` = v5 production with only `paths.*` changed; SEC-009 `authority inspect`
  CURRENT (ab28a92c...), covers all pending fields.
- Runner `run-p3d.sh` (same as A3's).
