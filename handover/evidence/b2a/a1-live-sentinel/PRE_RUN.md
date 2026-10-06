# A1 post-B2-a live sentinel: pre-run binding (committed before any model call)

- Kriya: fresh non-editable install of `b8809cd` (`kriya_version.json`, dirty false).
- Workspace: fresh clone of freezegun at `92d61b3` (`b2a-a1-py-freezegun`, never applied).
- Config: v5 production operator config, only `paths.skills/state/memory` changed (`config.diff`).
- Goal: byte-identical to the preserved A1 goal (`goal.txt`).
- Operator acceptance artifact (`acceptance.py.txt`, sha256 in `acceptance_sha256.txt`, file mode 0444): exactly the two
  behaviours the goal states, both mapped to REQ-1 - `freeze_time(0)` -> 1970-01-01 00:00:00 and
  `freeze_time(86400.5)` -> 1970-01-02 00:00:00.500000. No other case. Written by the operator harness, never by or
  shown to a model; Kriya binds and stores it before planning (`--acceptance`, `run-b2a.sh`).
- One run. Stop conditions: wrong candidate + SUCCESS/COMMIT/apply; acceptance case fails but BEHAVIOR closes; model
  claim or candidate test replaces acceptance evidence.
