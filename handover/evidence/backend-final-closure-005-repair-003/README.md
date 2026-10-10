# BACKEND-FINAL-CLOSURE-005 third repair cycle (repair-003) - preserved certification evidence

Certified executable revision: 3844540 (branch repair/c3-p1 fast-forwarded into main 2026-10-10; eleven commits over
5407a80, every SHA preserved). This directory is records only and was committed AFTER the certified revision (rule 23).

- CERTIFICATION.md - the certification record for 3844540 (operator-run full suite 9871 passed, ruff 0, pylint exit 0,
  both independent reviews and their dispositions, registry state, residuals, merge recommendation).
- CERTIFICATION_CANDIDATE_6d97558.md - the first candidate's record (history; superseded, evidence not transferred).
- full_suite_6d97558.txt.gz, full_suite_6169745.txt.gz, full_suite_3844540.txt.gz - the three operator-run canonical
  scripts/run_full_suite.py logs (9852 / 9868 / 9871 passed, exit 0), verbatim.
- reviews/ - REVIEWER_PROMPT_c3.md, REVIEW_RESULT_c3.md, REVIEW_DISPOSITION_c3.md (independent review of main..6d97558,
  APPROVE WITH CHANGES) and REVIEWER_PROMPT_c3_f1f3.md, REVIEW_RESULT_c3_f1f3.md, REVIEW_DISPOSITION_c3_f1f3.md (focused
  review of 6d97558..6169745, APPROVE WITH CHANGES); every finding dispositioned by the owner and applied.
- Pre-fix reproducer outputs, mutant outputs and scripts, and the F1 probe stay in the operator's evidence tree
  ~/kriya-m1-live/backend-final-closure-005/repair-003/{prefix,mutations,scratch}/ (never overwritten); their summary
  lines are quoted in handover/BACKEND_FINAL_CLOSURE_005_DESIGN.md sections 13.1-13.9 and the registry rows.
