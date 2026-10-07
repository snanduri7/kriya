# A3 live post-P3-D (owner-authorized, exactly one run): pre-invocation verification

Verified before invoking (MEASURED, 2026-10-06):
- Kriya: fresh non-editable install of the certified P3-D revision `bcb0c1d` (`kriya_version.json`: commit
  bcb0c1db84e7..., dirty false, embedded provenance) in `~/kriya-m1-live/venv-p3d` from a fresh clone `src-p3d`.
- Workspace `ws/b3-a3-java-lang`: HEAD 4ee346e59eecccdaefbdd74ac53698da4a1f348a (frozen base), no tracked change;
  only untracked entry is Kriya's own `.kriya/` control state from the earlier A3 run (same as before that run's attempt 2).
- Goal: `goals/b3-a3-java-lang.txt` byte-identical to the frozen A3 goal (cmp); sha256(bytes + NUL) = 06f0f1e9950a...
  == approval goal_sha256. Passed by `-f`.
- Acceptance sha256 a40b975f... and B3 approval sha256 49c05c59... == the approved artifacts (unchanged, not regenerated).
- Config `config/b3-a3-java-lang.yaml` (v5 production) unchanged; SEC-009 `authority inspect`: CURRENT, covers all
  pending fields.
- Runner `run-p3d.sh` = `run-b3f.sh` with only the env file changed (env-p3d.sh: PATH -> venv-p3d).
- Earlier A3 evidence preserved: `evidence/b3-a3-java-lang.5ab9d69`, `logs/b3-a3-java-lang.5ab9d69.*.log`.
