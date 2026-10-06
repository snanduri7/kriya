# RUN 1: A2 B2-COV sentinel (74d745f) - safety invariant held

Run `20261006T125843-1e9629b6`, M1 `VERIFIED`. Acceptance artifact bound at start, sha256 `a945ee26...`, unchanged
after the run. Terminal: `REQUIREMENTS_UNRESOLVED: REQ-1 (violated)` (enforce `original_requirements`), Quality Gates
FAILED; not committed, not applied (workspace HEAD = base `92d61b3`, no tracked change).

- Claim class: GENERAL ("of the form", "any other string").
- Attempt 1 refused (`diagnosis_mismatch`, proposed no change); attempt 2 staged the candidate (`candidate.diff`).
- REQ-2 mutation scope: closed. Model verifier: "Goal spec compliance PASSED" (non-authoritative).
- Acceptance: all three cases executed and failed -> `ACCEPTANCE_VIOLATED`, BEHAVIOR VIOLATED (a counterexample
  disproves a GENERAL claim; the GENERAL_RULE_UNPROVEN path did not arise because no case passed).
- C0 regression: not run (VIOLATED is final).

Independent check (diagnosis only; `independent_check.out`): the candidate calls `re.match` but `api.py` does not import
`re`, so `_parse_tz_offset` raises NameError for "+05:30", "-02:00", "abc", "+5:30" and "+05:30\n"; timedelta/number
offsets unchanged; freezegun's own suite 145 passed, 4 skipped (no test calls a string offset). Candidate: INCORRECT.
Kriya's VIOLATED is a genuine contradiction (the candidate's own code raised), not an infrastructure artifact.
