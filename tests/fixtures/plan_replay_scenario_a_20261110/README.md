# Sealed planner responses of replacement Scenario-A run 20261110T060714-704ab94f (FAIL - PLANNING_ERROR / KRIYA PLAN-VALIDATION DEFECT)

Extracted verbatim from the sealed attempt-evidence blobs (`~/kriya-evidence/product-72h-confidence/scenario-a-replacement-run-2026-11-10/raw/state/attempt-evidence/20261110T060714-704ab94f/`),
the planner's three `model.response` contents (planner attempt 0, repair 1, repair 2). Used for the MODEL-FREE replay in `tests/test_plan_validator_creator_consumer_001.py`; never sent to a model.

| file | sha256 prefix (first 16) | content blob |
|---|---|---|
| planner_attempt0.json | 6b83560b9e2531d2 | initial plan: s2/s3 both provide and require the same capability (schema invalid) |
| planner_attempt1.json | 1652ec2739423341 | repair 1: s1 over-claims provides (ambiguous provider); s1 creates pom.xml, s7 later modifies it |
| planner_attempt2.json | fd12cac1a634f84d | repair 2: the planner following the (defective) instruction `add s7 to s1.depends_on` |

The fixtures are test data only. No production code names this goal, these ids or these files.
