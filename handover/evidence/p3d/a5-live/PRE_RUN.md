# A5 live post-P3-D (owner-authorized, exactly one run): pre-invocation verification

Verified before invoking (MEASURED, 2026-10-06), no model call:
- Kriya `bcb0c1d` non-editable install (`venv-p3d`, dirty false), unchanged since A3/A4.
- Workspace `ws/p3v-a5-spring-xml`: HEAD 09351b3ee0bd5aec2d480c0280e84700978f56d3 (frozen base), `git status
  --ignored` empty (never run).
- Goal `goals/p3v-a5-spring-xml.txt`, unedited: sha256(bytes + NUL) = 3ee13b25... == approval goal_sha256. Passed by
  `-f` with an ABSOLUTE path.
- Acceptance: byte copy of the owner-reviewed `handover/evidence/b3/a345/A5_KriyaAcceptanceTest.java`, sha256
  d6bdc942... == approval acceptance_sha256 (3 cases).
- B3 approval: byte copy of the operator-controlled `approvals/A5_approval.json`, sha256 5f2318b6..., not regenerated
  (accept_suite_as_sufficient true, base 09351b3e).
- Dry bind with bcb0c1d (scratch state root): acceptance 3 cases for REQ-1, java; runner contract 36363c7d... ==
  approval; approval loads, covers REQ-1, base == HEAD.
- Config `config/p3v-a5-spring-xml.yaml` = v5 production, only `paths.*` changed; SEC-009 CURRENT (133d6d76...).
- Runner `run-p3d.sh` (same as A3/A4).
