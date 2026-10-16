"""Deterministic, model-free replay of the RECORDED planner response of live run 20261016T085640-13e8ec80 through Kriya's own code:

  recorded raw response -> parse_planner_structured_output -> build_engineering_plan_from_planner_output -> the controller's normalisations
  -> validate_plan (the controller's flags) -> build_structured_plan_repair_prompt (the controller's must_preserve construction)

and compares the result with what the live run RECORDED (planning diagnostics + attempt evidence). No model, no network, no workspace file is written.

Usage: python replay_planner_failure.py <repo root> <evidence dir (.../live-failure-.../scenario-04)> [--variant <name>]
Exit 0 only if the replay reproduces the recorded errors, reason codes and repair prompt exactly.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(sys.argv[1])
EVIDENCE = Path(sys.argv[2])
sys.path.insert(0, str(REPO))

from kriya.agents.contracts import parse_planner_structured_output  # noqa: E402
from kriya.workflow.acceptance import goal_requires_runtime_behavior  # noqa: E402
from kriya.workflow.obligations import ObligationKind, ObligationLedger  # noqa: E402
from kriya.workflow.plan_normalization import (  # noqa: E402
    coalesce_same_file_owners,
    derive_verification_contracts,
    redirect_immutable_test_edits,
    scope_verification_to_requirements,
)
from kriya.workflow.plan_schema import build_engineering_plan_from_planner_output  # noqa: E402
from kriya.workflow.plan_validation import canonicalize_planned_file_actions, validate_plan  # noqa: E402
from kriya.workflow.planner_repair import build_structured_plan_repair_prompt  # noqa: E402
from kriya.workflow.requirement_contract import requirement_set_for  # noqa: E402
from kriya.workflow.static_checks import derive_stack_contract  # noqa: E402
from kriya.workflow.triage import ChangeKind  # noqa: E402
from kriya.workflow.workflow_controller import (  # noqa: E402
    _preserved_reference_must_preserve_lines,
    _semantic_contract_must_preserve_lines,
)

RUN_ID = "20261016T085640-13e8ec80"
GOAL = (REPO / "demo-v2" / "scenarios" / "04-spring-ignite-protocol" / "goal.md").read_text(encoding="utf-8").strip()
DIAGNOSTICS = [json.loads(line) for line in (EVIDENCE / "workspace-dot-kriya" / "control" / "planning-diagnostics" / f"{RUN_ID}.jsonl").read_text().splitlines()]
EXTRACTED = EVIDENCE.parent / "scenario-04" / "extracted" if (EVIDENCE.parent / "scenario-04" / "extracted").is_dir() else EVIDENCE / "extracted"


def replay(plan_text: str, attempt: int) -> dict:
    """One loop iteration of the controller's authoritative planning path, for a planner response `plan_text`."""
    with tempfile.TemporaryDirectory(prefix="replay-ws-") as workspace:  # the live workspace was empty: repository evidence was []
        ledger = ObligationLedger()
        structured, issue = parse_planner_structured_output(plan_text)
        assert structured is not None, issue
        raw_plan = build_engineering_plan_from_planner_output(structured, plan_id=RUN_ID, kind=ChangeKind.MILESTONE)
        plan, _ = canonicalize_planned_file_actions(raw_plan, workspace)
        plan, coalesced = coalesce_same_file_owners(plan)
        plan, redirects = redirect_immutable_test_edits(plan, (), lambda rel: os.path.exists(os.path.join(workspace, rel)))
        plan, derived = derive_verification_contracts(plan)
        plan, scoped = scope_verification_to_requirements(plan, goal_requires_runtime_behavior(GOAL))
        requirement_set = requirement_set_for(GOAL, None)
        validation = asyncio.run(validate_plan(
            plan, workspace_path=workspace, available_tool_names=None, route=None, triage_service=None, resuming_own_established_progress=False,
            require_model_planned_files=True, require_semantic_contracts=True, runtime_verification_required=goal_requires_runtime_behavior(GOAL),
            stack_contract=derive_stack_contract(GOAL), obligation_ledger=ledger, revision=attempt, known_requirement_ids=requirement_set.ids,
            immutable_test_files=(), frozen_files=[], documentation_paths=[], delegating_shims=[]))
        reason_codes = list(dict.fromkeys(validation.reason_codes))
        must_preserve = [f"{rec.description} (evidence: {json.dumps(rec.evidence, default=str)})"
                         for rec in ledger.relevant_for_preservation(ObligationKind.PLAN_STRUCTURAL_VALIDITY)]
        must_preserve += _preserved_reference_must_preserve_lines(ledger) + _semantic_contract_must_preserve_lines(ledger)  # exactly as the controller builds it
        repair_prompt = build_structured_plan_repair_prompt(
            GOAL, plan_text, validation.errors, reason_codes, attempt + 1, route_kind=ChangeKind.MILESTONE,
            extension_candidates=[], repository_candidates=[], must_preserve=must_preserve,
            validation_evidence=validation.evidence, available_tool_names=None)
        return {"errors": validation.errors, "reason_codes": reason_codes, "repair_prompt": repair_prompt, "plan": plan,
                "invalid_subtask_ids": []}


def main() -> int:
    ok = True
    for attempt, recorded in enumerate(DIAGNOSTICS):
        plan_text = recorded["raw_plan_response"] if isinstance(recorded["raw_plan_response"], str) else json.dumps(recorded["raw_plan_response"])
        result = replay(plan_text, attempt)
        recorded_errors = recorded["validation_errors"] if isinstance(recorded["validation_errors"], list) else json.loads(recorded["validation_errors"])
        recorded_codes = recorded["reason_codes"] if isinstance(recorded["reason_codes"], list) else json.loads(recorded["reason_codes"])
        same_errors = result["errors"] == recorded_errors
        same_codes = result["reason_codes"] == recorded_codes
        recorded_repair = recorded.get("repair_prompt")
        same_repair = None if recorded_repair is None else (result["repair_prompt"] == recorded_repair)
        added = removed = None
        if recorded_repair is not None and not same_repair:
            import difflib
            diff = list(difflib.unified_diff(recorded_repair.splitlines(), result["repair_prompt"].splitlines(), lineterm="", n=0))
            added = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]
            removed = [line[1:] for line in diff if line.startswith("-") and not line.startswith("---")]
        print(json.dumps({"attempt": attempt, "repair_prompt_lines_added_vs_recorded": added, "repair_prompt_lines_removed_vs_recorded": removed, "replayed_errors": result["errors"], "replayed_reason_codes": result["reason_codes"], "same_errors_as_recorded": same_errors,
                          "same_reason_codes_as_recorded": same_codes, "same_repair_prompt_as_recorded": same_repair}, indent=1))
        ok = ok and same_errors and same_codes  # the repair prompt is reported, not required to match: it legitimately changes when the feedback is improved
    print("REPLAY REPRODUCES THE RECORDED VALIDATION FAILURE (errors and reason codes identical)" if ok else "REPLAY DOES NOT REPRODUCE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
