"""Final architecture reconciliation (2026-11-17, docs/architecture/13-FINAL-RECONCILIATION.md): the small product corrections that
closed registry rows, each with its negative control.

- ACCEPTANCE-ENVIRONMENT-ERROR-FLAG-NO-READER-001: the validator's ``acceptance_environment_error`` result field had three writers
  and no reader; it is gone, and the typed field its readers use (``environment_reason_code``) is unchanged.
- SEC009-AGENT-ROLE-REASON-TEXT-001: a field classified by a runtime override has a deliberate classification; the refusal says so
  instead of calling it an unknown field. The deny decision itself is unchanged.
- INT-09 (S7-APPROVAL-SUBJECT-RESIDUALS-001, item 5): a sensitive-path pattern that cannot be compiled is a configuration error, and
  at the reader it counts as matched; it used to be logged and skipped, so the path it was meant to protect raised no approval trigger.
- ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001: the pins of its disposition (a disposition removes an obligation and is never a success by
  itself; path authority is the goal's own FILE_IMMUTABILITY statement).
"""
import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from kriya.config.authority import ConfigSource, FieldClassification, compute_violations
from kriya.config.config import AppConfig
from kriya.policy.approval import TRIGGER_SENSITIVE_PATH, change_approval_triggers, sensitive_path_reason
from kriya.workflow.requirements import RequirementOutcome, derive_requirements, requirement_truth

KRIYA = Path(__file__).resolve().parents[1] / "kriya"


# --- ACCEPTANCE-ENVIRONMENT-ERROR-FLAG-NO-READER-001 -------------------------------------------------------------------------------
def _string_constants(path):
    return {node.value for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def test_the_readerless_acceptance_environment_flag_is_gone_and_the_typed_code_stays():
    writers = [str(path.relative_to(KRIYA)) for path in sorted(KRIYA.rglob("*.py"))
               if "acceptance_environment_error" in _string_constants(path)]
    assert writers == []
    # the field the readers use is still written by the validator and read outside it (the check above is not vacuous: it finds this one)
    holders = [str(path.relative_to(KRIYA)) for path in sorted(KRIYA.rglob("*.py"))
               if "environment_reason_code" in _string_constants(path)]
    assert "tools/validate.py" in holders and len(holders) > 1


# --- SEC009-AGENT-ROLE-REASON-TEXT-001 ---------------------------------------------------------------------------------------------
REPOSITORY = ConfigSource.AUTO_DISCOVERED_CWD


def test_a_dynamically_classified_role_field_is_refused_for_its_classification_not_as_an_unknown_field():
    field = ("agent_llms", "reviewer")
    [violation] = compute_violations({field: REPOSITORY}, {field: FieldClassification.SECURITY_AUTHORITY})
    assert violation.field_path == "agent_llms.reviewer" and violation.classification is FieldClassification.SECURITY_AUTHORITY
    assert f"classification {FieldClassification.SECURITY_AUTHORITY.value} requires a trusted or explicitly authorized source" in violation.reason
    assert "unknown/future field" not in violation.reason


def test_controls_an_unclassified_field_is_still_called_unknown_and_a_safe_override_is_no_violation():
    [unknown] = compute_violations({("agent_llms", "reviewer"): REPOSITORY})          # no override: no table names it
    assert "unknown/future field" in unknown.reason and unknown.classification is FieldClassification.SECURITY_AUTHORITY
    [future] = compute_violations({("no_such_section", "no_such_field"): REPOSITORY})
    assert "unknown/future field" in future.reason
    assert compute_violations({("agent_llms", "reviewer"): REPOSITORY},
                              {("agent_llms", "reviewer"): FieldClassification.REPOSITORY_SAFE}) == []


# --- INT-09: an invalid sensitive-path pattern --------------------------------------------------------------------------------------
INVALID = "src/(unclosed"


def test_an_invalid_sensitive_path_pattern_is_a_configuration_error_at_load():
    with pytest.raises(ValidationError) as refused:
        AppConfig(autonomy={"sensitive_paths": [r".*\.env$", INVALID]})
    assert "sensitive_paths" in str(refused.value) and "not a valid regular expression" in str(refused.value)
    assert AppConfig(autonomy={"sensitive_paths": [r"deploy/.*"]}).autonomy.sensitive_paths == [r"deploy/.*"]   # a valid one loads


def test_at_the_reader_a_pattern_that_cannot_be_evaluated_counts_as_matched_and_requires_approval():
    reason = sensitive_path_reason(["src/app.py"], [INVALID])
    assert reason and "cannot be evaluated" in reason and INVALID in reason
    triggers = change_approval_triggers(human_in_the_loop=False, sensitive_match=bool(reason), diff_lines=1, risk_threshold_lines=100,
                                        process_profile_requires_review=False)
    assert triggers == [TRIGGER_SENSITIVE_PATH]


def test_controls_valid_patterns_match_exactly_as_before():
    assert sensitive_path_reason(["deploy/prod.yaml"], [r"deploy/.*"]) == "Sensitive path matched: deploy/prod.yaml (deploy/.*)"
    assert sensitive_path_reason(["src/app.py"], [r"deploy/.*", r".*\.env$"]) == ""
    assert sensitive_path_reason([], [INVALID]) == ""                     # nothing changed: nothing to protect, nothing to evaluate
    assert sensitive_path_reason(["DEPLOY/x"], [r"deploy/.*"]) != ""       # case-insensitive, as before


# --- ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001 ---------------------------------------------------------------------------------------
GOAL = "CSVFormat.EXCEL produces spurious empty records for blank lines.\nEvery existing test must keep passing unchanged.\n"


def test_a_dispositioned_statement_is_never_a_success_by_itself():
    """The false-success half of the row: a disposition removes a statement from the mandatory set; it verifies nothing. With every
    statement dispositioned there is NO mandatory requirement and the truth is OPEN (never ALL_VERIFIED over zero requirements); with
    another requirement still unverified the truth is OPEN; only the other requirement's own evidence resolves the run."""
    requirements = derive_requirements(GOAL)
    first, second = [requirement.id for requirement in requirements.requirements]
    dispositioned = RequirementOutcome.DISPOSITIONED
    nothing_mandatory = requirement_truth({first: dispositioned, second: dispositioned}, requirements)
    assert nothing_mandatory["status"] == "OPEN_REQUIREMENTS" and nothing_mandatory["total"] == 0
    still_open = requirement_truth({first: dispositioned, second: RequirementOutcome.UNVERIFIED}, requirements)
    assert still_open["status"] == "OPEN_REQUIREMENTS" and [item["id"] for item in still_open["unresolved"]] == [second]
    resolved = requirement_truth({first: dispositioned, second: RequirementOutcome.CLOSED_BY_EVIDENCE}, requirements)
    assert resolved["status"] == "ALL_VERIFIED" and (resolved["verified"], resolved["total"]) == (1, 1)
    violated = requirement_truth({first: dispositioned, second: RequirementOutcome.VIOLATED}, requirements)
    assert violated["status"] == "CONTRADICTED"
