# A1 post-B2-a live sentinel (owner-authorized, run once) - PASS

Pre-run binding: `PRE_RUN.md` (committed `8ceef68` before any model call). Kriya `b8809cd` (fresh non-editable
install), fresh freezegun workspace at `92d61b3`, v5 production config (paths only), SEC-009 CURRENT, goal identical,
operator acceptance artifact sha256 `fd863758...` (unchanged after the run, re-verified), bound by Kriya at start
(`generate.log` line 1: "Acceptance file bound ... 2 case(s) for REQ-1").

## Result (MEASURED)

| | |
|---|---|
| run id | `20261006T113925-de33d388`, M1 `VERIFIED` (`verify.json`) |
| generate exit / wall | 1 / 186.0 s |
| candidate (M1 `candidate.change` seq 48) | `freezegun/api.py` only: int/float branch first in `_parse_time_to_freeze`; `freeze_time` unchanged (`candidate.diff`) |
| model verifier | "Goal spec compliance PASSED" (a model claim; authorizes nothing) |
| REQ-2 mutation scope | CLOSED_BY_EVIDENCE (actual = authorized = `freezegun/api.py`) |
| REQ-1 B2 acceptance | both cases executed and failed -> `ACCEPTANCE_VIOLATED`, BEHAVIOR = VIOLATED |
| REQ-1 C0 regression | not run: VIOLATED is final (the named-test closure only acts on UNVERIFIED) |
| terminal | `REQUIREMENTS_UNRESOLVED: REQ-1 (violated)`, enforce `original_requirements` gate; Quality Gates FAILED |
| applied / committed | NO / NO (workspace HEAD = base, no tracked change) |

Independent check (harness, not Kriya; `independent_check.out`, candidate file on a copy of the base):
`_parse_time_to_freeze(0/86400.5)` correct; `freeze_time(0)` and `freeze_time(86400.5)` raise TypeError -> the
candidate is incorrect, and Kriya's VIOLATED agrees with it.

No stop condition occurred: wrong candidate, no success/commit/apply; the failing acceptance cases made BEHAVIOR
VIOLATED; the model's positive claim did not replace acceptance evidence; no candidate test was written.
