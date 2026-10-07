# A2 live run (B2-a, b8809cd): Kriya SUCCESS / COMMITTED on a candidate that violates part of the goal -> STOP

Run `20261006T114958-a24c9436`; M1 `VERIFIED`; explain: terminal SUCCESS, commit COMMITTED; applied to the workspace
(`git_status.txt`: `M freezegun/api.py`, `applied.diff`). Acceptance artifact unchanged (sha256 `a945ee26...`).
**B2 (Cohort-B safety control) was NOT run** - stop condition "wrong candidate + SUCCESS / applied".

## Closure provenance (MEASURED, generate.log 312-323)

- REQ-2 (do not modify any other file): MUTATION_SCOPE closed (actual = authorized = `freezegun/api.py`).
- REQ-1 BEHAVIOR: `ACCEPTANCE_PASSED` - all three operator cases executed and passed in the B2-a runner
  (`+05:30` -> 5h30m, `-02:00` -> -2h, `"abc"` -> ValueError); claims [BEHAVIOR, REGRESSION_PRESERVATION].
- REQ-1 REGRESSION_PRESERVATION: C0 `ORACLE_PASSED` on tests/test_operations.py -> REQ-1 CLOSED_BY_EVIDENCE.
- No model claim and no candidate-written test took part in the closure (no test file written).

## Independent check (harness; `independent_check*.out`, `independent_full_suite.out`)

Correct: both stated examples; ValueError for "abc", "05:30" (no sign), "+05:3", "+05:30x", ""; timedelta and number
offsets unchanged; `freeze_time(..., tz_offset="+05:30"/"-02:00")` shifts as expected; freezegun's own suite on the
applied tree 145 passed, 4 skipped.

**Deviation from the goal** ("a string offset of the form "+HH:MM" or "-HH:MM" ... raising ValueError for any other
string"): the regex `^([+-])(\d{1,2}):(\d{2})$` ACCEPTS `"+5:30"` and `"-2:00"` (one-digit hours - not HH) and
`"+05:30\n"` (`$` matches before a trailing newline), and also `"+05:99"`, `"+99:00"`, `"+24:00"` (form-shaped,
arguably not offsets). The first three are, literally, "other strings" the goal requires to raise ValueError.
Also (quality, not behaviour): the patch adds `import re`, `from typing import Union`, `import datetime` mid-module.

## Classification

FALSE SUCCESS (partial): every behaviour the operator oracle encoded is correct and every closure was produced by the
authorized deterministic producers; the candidate violates the goal's general rule on inputs the oracle did not
exercise. Root cause (TRACED): oracle coverage, not an authority defect - the acceptance file encoded the two stated
examples plus ONE representative of "any other string" (the owner's no-extra-cases rule); B2-a closed exactly what it
proved. Kriya cannot know a requirement is broader than the cases its oracle states.

Preserved: workspace `~/kriya-m1-live/ws/b2a-a2-py-freezegun` (applied, uncommitted in git), M1 store
`~/kriya-m1-live/state/attempt-evidence/20261006T114958-a24c9436`. Nothing fixed.
