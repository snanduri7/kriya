# A3 live (model-bearing run, owner re-invocation): pre-invocation verification

Attempt 1 was a pre-run binding refusal (0 model calls, no run id) - recorded separately, not counted as the live run.
This invocation passes the frozen goal FILE to Kriya with `-f` (`run-b3f.sh`), never through `"$(cat ...)"`.

Verified before invoking (MEASURED):
- Kriya's goal digest is `sha256(goal + "\0" + "\0".join(clarifications))` (requirements.derive_requirements /
  _goal_digest; no clarifications): sha256(the -f file bytes + NUL) = 06f0f1e9...b447 == the approval's goal_sha256.
  (A raw sha256 of the file bytes, 637297d6..., is not that digest by definition.) The file is 419 bytes and ends with
  a newline; it is the frozen A3 goal, unedited.
- acceptance file sha256 a40b975f... (approved); approval sha256 49c05c59... == the approved copy; workspace HEAD
  4ee346e59eecccdaefbdd74ac53698da4a1f348a, clean; SEC-009 CURRENT; Kriya 5ab9d69, dirty false.

Deferred (owner): B3-UX-1 - the goal approval fingerprint is byte-sensitive to a trailing newline / invocation transport;
current effect fail-closed; any future canonicalization must be one explicit versioned contract for goal ingestion,
approval creation, approval validation and resume identity.
