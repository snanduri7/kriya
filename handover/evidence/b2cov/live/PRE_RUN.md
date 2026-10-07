# B2-COV live validation: pre-run binding (committed before any model call)

Kriya: fresh non-editable install of the certified `74d745f` (`kriya_version.json`). v5 production config, only
`paths.*` changed (`*.config.diff`). Fresh workspaces: freezegun @ `92d61b3` (`b2cov-a2-py-freezegun`), cssselect2 @
`dc2690c` (`b2cov-b2-py-cssselect2`). Goals byte-identical to the post-P3 selection. Order: A2, then Cohort-B; stop
after both, or after A2 on SUCCESS/COMMIT/APPLY.

- RUN 1 A2 sentinel: the existing operator acceptance artifact, byte-identical (sha256 a945ee26..., `a2_acceptance_sha256.txt`):
  "+05:30", "-02:00", "abc" -> ValueError. No case added, removed or changed. Expected: GENERAL ->
  ACCEPTANCE_GENERAL_RULE_UNPROVEN (or VIOLATED), REQUIREMENTS_UNRESOLVED, no success/commit/apply.
- RUN 2 Cohort-B control: no `--acceptance` (no file for the task; `run-b2cov.sh` passes it only when one exists),
  no approval, no new oracle. Expected: BEHAVIOR UNVERIFIED, blocked, no commit.
