# D6 + D7 evidence

Product commits `3f15f65` (D6) and `00efca6` (D7). Both found on demo-runtime-4 (4834164); the runs are kept
unchanged in `~/kriya-live-demo/demo-01-skills/evidence/v4/` (`run-a-20261001T121651Z`, `run-b-20261001T122407Z`,
each with CLASSIFICATION.md).

## D6: RUNTIME-ARTIFACTS-EPHEMERAL-001

- `fixtures/knowb_candidate/`: KNOW B's three candidate files, taken verbatim from that run's log through the
  production parser.
- `d6_reproducer.py`: model-free. A git repo holding the candidate, a bound VerificationTreeBinding, and
  `PolymorphicValidator.run_app_sequence` with KNOW B's exact command (host execution; Maven and Ignite 2.18 from
  the local repository).
  - `d6_reproducer_prefix.txt`: `VerificationGateCreatedFiles` / `VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE`,
    likely_files `[ignite/README.txt]` (what the live run then offered to recovery).
  - `d6_reproducer_postfix.txt`: gate PASSED, `[VERIFICATION] PASS`, Ignite started,
    `runtime_artifacts=["ignite/README.txt"]`, nothing left in the workspace.

## D7: DIAGNOSIS-REMOVAL-MISMATCH-001

- `fixtures/knowa_attempt{1_file,4_edit}_response.txt`: KNOW A's original file response and attempt-4 edit response,
  verbatim from that run's log.
- `d7_reproducer.py`: replays them through `parse_structured` and `find_edits_ignoring_own_diagnosis`. The call is
  in the original and in the edit's SEARCH, absent from its REPLACE.
  - `d7_reproducer_prefix.txt`: `DIAGNOSIS MISMATCH`.
  - `d7_reproducer_postfix.txt`: accepted.

## Verification

- `mutate_d6_d7.py` / `mutation_d6_d7.txt`: 16/16 killed: 10 D6, covering every mutation on the owner's list plus
  evidence and the exception path, and 6 D7 on the exact decision branch.
- `full_suite_00efca6_tail.txt`: 8030 passed, 0 failed, 0 skipped, 14 warnings (the same fork DeprecationWarnings).
  The new tests under `-W error::ResourceWarning`: 35 passed, none emitted.
- `doctor_v4_00efca6.json`: v4 config unchanged, run serially from the KNOW A workspace: production_ready=true;
  WARNs as at the earlier dev-checkout doctors.
