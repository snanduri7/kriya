"""Kernel golden evidence (full modularization E1): the preservation proof for every W4 E-slice.

Each scenario in ``_kernel_golden.SCENARIOS`` drives one terminal path of the real kernel with only the model
scripted, and its observable outcome (ordered attempt-evidence record kinds with payload/content digests, every
RunRecord, the traces.db run events, the WorkflowResult / result dict) is compared byte for byte with
``tests/golden/kernel/<scenario>.json``. The goldens were WRITTEN FROM 4eaf610 (before any W4 production edit) and
generated three times under PYTHONHASHSEED 1, 2 and 3 with identical bytes. An E-slice (PRESERVE) must leave every
file unchanged; a diff means the extraction changed observable behaviour. Regenerate ONLY from a commit whose
behaviour is known good: ``KRIYA_WRITE_KERNEL_GOLDEN=<dir> .venv/bin/pytest tests/state_machine/test_sm_kernel_golden.py``
writes the forms to ``<dir>`` (and fails, so a write is never mistaken for a pass); review the diff, then copy.
"""
import json
import os
from pathlib import Path

import _kernel_golden as kg
import pytest

pytestmark = pytest.mark.state_machine


@pytest.fixture(autouse=True)
def _fixed_git_identity(monkeypatch):
    for name, value in kg.GIT_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("name", sorted(kg.SCENARIOS))
def test_the_kernel_scenario_matches_its_pre_decomposition_golden(name, tmp_path):
    captured = kg.capture(kg.SCENARIOS[name](tmp_path), tmp_path, name)
    form = kg.render(captured)
    contents = os.environ.get(kg.CONTENT_ENV)
    if contents:
        Path(contents).mkdir(parents=True, exist_ok=True)
        (Path(contents) / f"{name}.content.json").write_text(kg.render({"contents": kg.form_contents[id(captured)]}),
                                                               encoding="utf-8")
    target = os.environ.get(kg.WRITE_ENV)
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / f"{name}.json").write_text(form, encoding="utf-8")
        pytest.fail(f"golden form written to {target}/{name}.json; rerun without {kg.WRITE_ENV}")
    golden = (kg.GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8")
    assert form == golden, f"{name}: the kernel's observable outcome differs from tests/golden/kernel/{name}.json"


# What each golden must show for its terminal path (read from the committed file: a regenerated golden whose run
# silently took another path fails here, not only on the byte comparison). (result type, quality_gates_passed,
# failure_category, status, RunRecord lifecycles in order, whether an attempt was opened)
EXPECTED = {
    "direct_success": ("dict", True, None, None, ["SUCCESS"], True),
    "legacy_success": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_fallback_success": ("WorkflowResult", True, None, None, ["SUCCESS"], True),
    "legacy_gates_exhausted": ("WorkflowResult", False, "quality_gates_exhausted", None, ["FAILURE"], True),
    "legacy_repeated_failure_exhausted": ("WorkflowResult", False, "quality_gates_exhausted", None, ["FAILURE"], True),
    "legacy_no_progress": ("WorkflowResult", False, "no_progress", None, ["FAILURE"], True),
    "legacy_budget_stop": ("WorkflowResult", False, "generation_budget_exhausted", None, ["FAILURE"], True),
    "legacy_final_review_refused": ("WorkflowResult", False, "final_review_refused", None, ["FAILURE"], True),
    "legacy_approval_refused": ("WorkflowResult", False, None, None, ["FAILURE"], True),
    "legacy_resume_after_failure": ("WorkflowResult", True, None, None, ["FAILURE", "SUCCESS"], True),
    "enforce_success_with_verification_unit": ("WorkflowResult", True, None, "success", ["SUCCESS"], True),
    "enforce_approval_refused": ("WorkflowResult", False, None, "failed", ["FAILURE"], True),
    "enforce_plan_invalid": ("WorkflowResult", False, None, "needs_review", ["FAILURE"], False),
    "enforce_verification_only_failed": ("WorkflowResult", False, None, "failed", ["FAILURE"], True),
    "milestone_chain_completed": ("dict", None, None, "success", ["SUCCESS"], False),
    "milestone_rerun_reuses_completed": ("dict", None, None, "success", ["SUCCESS", "SUCCESS"], False),
    "milestone_resume_after_recovered_crash": ("dict", None, None, "success", ["RECOVERED", "SUCCESS"], False),
}


def test_every_golden_shows_its_terminal_path_and_real_evidence():
    assert sorted(EXPECTED) == sorted(kg.SCENARIOS) == sorted(kg.TERMINAL_PATHS)
    for name, (kind, passed, category, status, lifecycles, attempted) in EXPECTED.items():
        golden = json.loads((kg.GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
        values = golden["result"]["legacy_result"]["values"]
        assert golden["_scenario"]["terminal_path"] == kg.TERMINAL_PATHS[name], name
        assert golden["_scenario"]["generated_from"] == kg.GENERATED_FROM, name
        assert (golden["result"]["type"], values.get("quality_gates_passed"), values.get("failure_category"),
                values.get("status")) == (kind, passed, category, status), name
        assert [record["lifecycle_state"] for record in golden["run_records"]] == lifecycles, name
        kinds = [record["kind"] for store in golden["evidence"] for record in store["records"]]
        assert ("attempt.opened" in kinds) is attempted, name
        if not name.startswith("milestone_"):   # the FakeEngine milestone runs make no model call: no evidence store
            assert kinds[0] == "run.opened" and "run.closed" in kinds, name
            assert all(store["verification"] == "VERIFIED" and store["sealed"] for store in golden["evidence"]), name
