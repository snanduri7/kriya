# S3 decision-to-killer table (correction unit S3, TYPE C: config authority for enabling fields; OI-99, OWNER-RECOVERY-SELF-CORRECTION-REVIEW-001)

Mutants: `~/kriya-modularization-tools/mutrun.py` v2, deselect as S1/S2. L1 = tests/test_s3_config_authority_enabling_fields.py,
L2 = test_sec009_config_authority, test_sec009_p2_authority_approval, test_e11_owner_modules, test_kup_config_approval,
test_kup_project_init, L3 = test_workflow_controller_enforce, contracts/test_config_resolution_contract, test_config.

Campaigns (MEASURED): `20261019_150549_66ae87f3` and `20261019_150728_28cf915b` at 1304251: 15/15 killed (14 L1, S3M15 at L2)
but INVALID: a session-detached Python child of an L2 file outlived the ladder's -x stop (straggler; tree_diff empty) -
a mutrun/harness artifact, not product (TEST_HARNESS). L1 gap closed in 89c6550 (a disabled loop never runs, in every
mode); `20261019_150926_dddb846a` at 89c6550: 15/15 killed at L1, VALID, 46 s.

Reproduction (MEASURED, scratch `s235/s3/{before,after}.txt`): before, a workspace kriya.yaml containing only
`autonomy: {self_correction_loop_enabled: true}` loaded (mode human-in-the-loop, enabled True) with 0 pending violations,
and the owner-recovery loop ran in human-in-the-loop and guardrails. After: ConfigAuthorityError naming
autonomy.self_correction_loop_enabled (security_authority), 1 pending violation; the loop is held in
human-in-the-loop (returns None, falls through to the governed Developer path) and still runs in guardrails.

| decision | symbol | risk | killer test | mutation | result | evidence |
|---|---|---|---|---|---|---|
| the enabling field is not REPOSITORY_SAFE | `_REPOSITORY_SAFE_FIELDS` | a repository enables the loop silently | test_a_value_gated_field_is_safe_only_at_its_restricting_bool, test_every_repository_safe_field_was_audited | S3M01 back in the safe table | KILLED L1 | c3 |
| the sibling (auto-approve) likewise | same | a repository removes the egress confirmation | same [web_lookup_auto_approve] | S3M02 | KILLED L1 | c3 |
| any non-restricting value needs authority | `value_gated_field_classification` | `true` accepted | test_every_enabling_spelling_is_refused | S3M03 always safe | KILLED L1 | c3 |
| identity with the bool, not equality/truthiness | same `is` | `0` / `None` / `""` treated as the safe False | test_a_value_gated_field_is_safe_only_at_its_restricting_bool | S3M04 `==`, S3M05 `not value` | KILLED L1 (x2) | c3 |
| the restricting value needs nothing (init profiles unchanged) | same | every init'd project re-asks / stale approvals | test_the_restricting_value_from_the_repository_needs_no_approval, test_a_project_setup_profile_asks_for_exactly_the_same_approvals | S3M06 never safe, S3M08 gate entry dropped | KILLED L1 (x2) | c3 |
| a field that is not gated keeps its static class | same `None` | gate widens unrelated fields | test_a_project_setup_profile_asks_for_exactly_the_same_approvals | S3M07 unlisted -> safe | KILLED L1 | c3 |
| resolution applies the override | `resolution.resolve_config_state` | static SECURITY applies to False too | test_the_restricting_value_from_the_repository_needs_no_approval | S3M09 override skipped | KILLED L1 | c3 |
| a skipped override fails CLOSED | `_SECURITY_AUTHORITY_FIELDS` | static fallback would be "unknown" only | test_a_value_gated_field_is_safe_only_at_its_restricting_bool (is_known_field) | S3M10 static entry removed | KILLED L1 | c3 |
| human-in-the-loop holds the owner-recovery loop | `owner_recovery._attempt_owner_recovery_self_correction` | unreviewed model bytes compiled | test_in_human_in_the_loop_owner_recovery_never_runs_the_unreviewed_loop | S3M11 removed | KILLED L1 | c3 |
| other modes keep the approved loop | same | the opt-in disappears everywhere | test_an_approved_loop_outside_human_in_the_loop_still_runs | S3M12 inverted | KILLED L1 | c3 |
| the decision is AUTHORITY's (`policy.approval`) | same | a second, local mode authority | test_the_hold_is_the_authority_decision_not_a_local_mode_comparison | S3M13 local string compare | KILLED L1 | c3 |
| the hold is typed and logged | `OWNER_RECOVERY_SELF_CORRECTION_HELD_FOR_SOURCE_REVIEW` | silent skip | test_in_human_in_the_loop_owner_recovery_never_runs_the_unreviewed_loop | S3M14 no log | KILLED L1 | c3 |
| disabled stays disabled | same | the hold check replaces the opt-in | test_a_disabled_loop_never_runs_in_any_mode | S3M15 enable check removed | KILLED L1 (c3; L2 in c1/c2) | c1-c3 |
