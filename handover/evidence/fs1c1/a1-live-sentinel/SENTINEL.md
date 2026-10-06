# A1 FS-1C1 live sentinel (owner-authorized, run once)

**Kriya:** non-editable install of `dfe22f8` (FS-1C1 implementation; `kriya_version.json`: dirty false), fresh clone in
`~/kriya-m1-live/src-fs1c1`. **Workspace:** fresh clone of freezegun at `92d61b3` (`fs1c1-a1-py-freezegun`, never
applied before). **Config:** v5 production operator config, only `paths.skills/state/memory` changed (`config.diff`).
SEC-009: owner approval, `authority inspect` = CURRENT before the run. **Goal:** byte-identical to the preserved A1
goal (`goal.txt`). Gates and models unchanged. One run, no rerun.

## Result (MEASURED)

| | |
|---|---|
| run id | `20261006T094757-e713dddd` (M1 store VERIFIED, `verify.json`) |
| generate exit | 1, wall 224.2 s |
| REQ-2 (do not modify any other file) | CLOSED_BY_EVIDENCE - MUTATION_SCOPE, actual = authorized = `freezegun/api.py` |
| REQ-1 regression claim | C0 `ORACLE_PASSED` on tests/test_operations.py, recorded as REGRESSION_PRESERVATION only |
| REQ-1 behaviour claim | UNVERIFIED (`REQUIREMENT_BEHAVIOR_UNVERIFIED`: no independent behaviour evidence) |
| terminal | `REQUIREMENTS_UNRESOLVED` (enforce `original_requirements` gate), `NOT_COMMITTED` |
| applied | NO - workspace HEAD = base `92d61b3`, no tracked change (`git_status.txt` empty) |

## The candidate Kriya refused (independent check, harness - not Kriya)

`candidate.diff` (from the M1 store blob, `candidate.change` seq 53): the same helper-only change as the post-P3 false
success - a new int/float branch in `_parse_time_to_freeze`. `independent_check.out` on a copy of the base with that
candidate file: `_parse_time_to_freeze(0/86400.5)` correct; `freeze_time(0)` and `freeze_time(86400.5)` raise
TypeError. The goal is not met, and Kriya did not report success.

**A1 FS-1C1 SENTINEL = PASS** (expected: regression closed by C0, scope closed, behaviour UNVERIFIED,
REQUIREMENTS_UNRESOLVED, no success/commit/apply - all observed).

Note (harness): after the run, the independent check installed `python-dateutil` into the harness venv
`venv-fs1c1` (needed to import freezegun outside Kriya). The run itself was complete before that.
