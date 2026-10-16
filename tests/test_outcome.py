"""Run outcome construction (full modularization E2): the terminal ``run_generation_workflow`` result and the run's
final traces.db row, on every terminal path, are what they were before the outcome/lifecycle code left
``workflow.py``.

Part 1 (characterization, written and green at d739de2 BEFORE any E2 production edit):

- every engine-driven E1 golden scenario's result section (``WorkflowResult`` fields, or the direct dict) equals
  its committed golden field by field (the byte comparison of the whole form stays in
  ``tests/state_machine/test_sm_kernel_golden.py``; this names the field that differs);
- every ``failure_category`` the terminal ladder can produce from an ``environment_failure`` stop, end to end: a
  real scripted run (``_kernel_golden._scripted``, engine entry) whose compile gate raises the orchestrator stop
  carrying that prefix, asserting the result dict and the final traces.db row. E1 lists static-analysis BLOCKED as
  an uncovered terminal path; its classification is one of these rows. The injection seam is
  ``PolymorphicValidator.run_compile_check`` (not a kernel module, so no kernel patch point is added).
"""
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position

from kriya.core.provider_contract import PROVIDER_PROMPT_TRUNCATED  # noqa: E402
from kriya.core.state_paths import trace_db_path  # noqa: E402
from kriya.workflow.contract_lifecycle import CONTRACT_REGISTRY_CORRUPT  # noqa: E402
from kriya.workflow.failure import Failure, QualityGateFailure  # noqa: E402
from kriya.workflow.file_integrity import (  # noqa: E402
    MIXED_NEWLINE_UNSUPPORTED,
    VERIFICATION_GATE_MUTATED_TRACKED_FILES,
)
from kriya.workflow.requirements import REQUIREMENTS_UNRESOLVED  # noqa: E402

# The E1 scenarios whose terminal result the engine builds (the milestone ones drive a FakeEngine).
ENGINE_SCENARIOS = sorted(name for name in kg.SCENARIOS if not name.startswith("milestone_"))


@pytest.fixture(autouse=True)
def _fixed_git_identity(monkeypatch):
    for name, value in kg.GIT_ENV.items():
        monkeypatch.setenv(name, value)


def _golden(name: str) -> dict:
    return json.loads((kg.GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ENGINE_SCENARIOS)
def test_the_terminal_result_of_every_engine_path_equals_its_golden_field_by_field(name, tmp_path):
    captured = kg.capture(kg.SCENARIOS[name](tmp_path), tmp_path, name)
    produced = json.loads(kg.render({"result": captured["result"]}))["result"]
    golden = _golden(name)["result"]
    assert produced["type"] == golden["type"]
    assert produced.get("fields") == golden.get("fields")
    assert produced["legacy_result"]["keys"] == golden["legacy_result"]["keys"], name
    differing = sorted(key for key in golden["legacy_result"]["values"]
                       if produced["legacy_result"]["values"].get(key) != golden["legacy_result"]["values"][key])
    assert differing == [], f"{name}: result fields differ from the golden: {differing}"
    others = sorted(key for key in golden if key not in ("type", "fields", "legacy_result")
                    and produced.get(key) != golden[key])
    assert others == [], f"{name}: WorkflowResult fields differ from the golden: {others}"


# environment_failure prefix -> the result's failure_category (workflow.py's terminal ladder, read at d739de2).
CATEGORY_BY_STOP = {
    "UNAUTHORIZED_GENERATION_TARGET: injected": "unauthorized_generation_target",
    "NO_AUTHORIZED_REPAIR_TARGET: injected": "unauthorized_generation_target",
    "CANDIDATE_INDEPENDENT_DETERMINISTIC_FAILURE: injected": "candidate_independent_deterministic_failure",
    "GENERATION TIME BUDGET EXHAUSTED: injected": "generation_budget_exhausted",
    "CONTAINMENT_SETUP_FAILED: injected": "containment_setup_failed",
    "REGRESSION_UNATTRIBUTED: injected": "regression_unattributed",
    "FALLBACK_MODEL_INCOMPATIBLE: injected": "fallback_model_incompatible",
    "CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE: injected": "context_edit_protocol_unsatisfiable",
    f"{PROVIDER_PROMPT_TRUNCATED}: injected": "provider_contract_violation",
    "RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED: injected": "retry_identity_not_qualified",
    f"{REQUIREMENTS_UNRESOLVED}: injected": "requirements_unresolved",
    f"{CONTRACT_REGISTRY_CORRUPT}: injected": "contract_registry_blocked",
    CONTRACT_REGISTRY_CORRUPT: "contract_registry_blocked",     # the exact-code form (no detail)
    "STATIC_ANALYSIS_BLOCKED: injected": "static_analysis_blocked",
    "STATIC_ANALYSIS_UNKNOWN: injected": "static_analysis_unknown",
    "STATIC_ANALYSIS_UNAVAILABLE: injected": "static_analysis_unavailable",
    "WORKSPACE_COMMIT_NOT_COMPLETED: injected": "workspace_commit_failed",
    f"{MIXED_NEWLINE_UNSUPPORTED}: injected": "file_integrity_unsupported",
    f"{VERIFICATION_GATE_MUTATED_TRACKED_FILES}: injected": "verification_tree_mutated",
    "TOOLCHAIN_MISSING: injected": "environment_failure",       # no typed prefix: the generic environment stop
}


def _final_row(cfg) -> dict:
    conn = sqlite3.connect(trace_db_path(cfg))
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone())
    finally:
        conn.close()


@pytest.mark.parametrize("stop", sorted(CATEGORY_BY_STOP))
def test_every_environment_stop_reaches_the_result_and_the_trace_row_as_its_category(stop, tmp_path):
    def gate_stops(*_args, **_kwargs):
        raise QualityGateFailure(Failure(type="time_budget_exhausted", source="orchestrator", message=stop, attempt=1))

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", side_effect=gate_stops):
        outcome = kg._scripted(tmp_path, lambda model, n, text: kg.GOOD, controller=False)
    result = outcome.result
    category = CATEGORY_BY_STOP[stop]
    assert (result["failure_category"], result["environment_failure"], result["quality_gates_passed"]) == (
        category, stop, False)
    assert result["completion_kind"] is None and result["no_progress_reason"] is None
    assert result["last_failure"]["type"] == "time_budget_exhausted" and result["last_failure"]["message"] == stop
    assert "final_review_refusal" not in result
    assert sorted(result) == _golden("direct_success")["result"]["legacy_result"]["keys"]
    row = _final_row(outcome.cfg)
    assert (row["status"], row["failure_category"]) == ("failure", category)
    assert [entry["category"] for entry in json.loads(row["failure_report"] or "[]")] == [
        entry["category"] for entry in result["failure_report"]]
    kinds = [event["kind"] for event in json.loads(row["run_events"] or "[]")]
    assert kinds[:3] == ["egress.authority", "evidence.attempt_store", "model.runtime"], kinds
