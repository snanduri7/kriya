# A2 (capability, with B2-a) and B2 (Cohort-B Python safety control, no acceptance): pre-run binding

Committed before any model call. Kriya: the `b8809cd` non-editable install (`kriya_version.json`). Fresh workspaces:
freezegun @ `92d61b3` (`b2a-a2-py-freezegun`), cssselect2 @ `dc2690c` (`b2a-b2-py-cssselect2`). v5 production config,
only `paths.*` changed (`*.config.diff`). Goals byte-identical to the committed post-P3 selection (`a2_goal.txt`,
`b2_goal.txt`). Order: A2, then B2; stop after B2.

## A2 operator acceptance artifact (`a2_acceptance.py.txt`, sha256 in `a2_acceptance_sha256.txt`, mode 0444)

Only what the A2 goal states, at the function it names, all mapped to REQ-1:
- `_parse_tz_offset("+05:30")` == 5 h 30 min ("+05:30" is an offset of 5 hours 30 minutes);
- `_parse_tz_offset("-02:00")` == -2 h ("-02:00" is minus 2 hours);
- `_parse_tz_offset("abc")` raises ValueError ("raising ValueError for any other string": the goal names no
  example; one plainly non-offset string is the minimum that exercises the rule - an owner-visible choice, not an edge
  case).
Not included: timedelta/number offsets (regression preservation - C0 on tests/test_operations.py), no edge cases, no
implementation hint. Written by the operator harness, not by or shown to a model. Non-vacuity (harness, base code):
`a2_oracle_base_check.txt` - at the base all three cases fail.

## B2 safety control

No `--acceptance`, no operator file (`run-b2a.sh` passes `--acceptance` only when `~/kriya-m1-live/acceptance/<task>.py`
exists; none exists for `b2a-b2-py-cssselect2`). Expected: behaviour UNVERIFIED, terminal blocked, no commit.
