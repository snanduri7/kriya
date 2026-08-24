"""MA5.7: ContextOrchestrator (kriya/workflow/context_orchestrator.py) -
select_context_strategy's real (kind, weight) dispatch table, the token-
budget-aware trim/omit logic, and a real end-to-end build() call."""

import asyncio

from kriya.agents.contracts import AcceptanceCriterion, MilestoneMode, MilestoneV2, ProvidedCapability
from kriya.control.state import ControlState
from kriya.workflow.context_orchestrator import (
    ContextStrategy,
    ContextOrchestrator,
    select_context_strategy,
)
from kriya.workflow.process_profile import HEAVY_PROFILE, LIGHT_PROFILE, STANDARD_PROFILE
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass


def _route(kind, weight):
    return EngineeringRoute(
        kind=kind, impact=ImpactVector(),
        initial_risk_class=RiskClass.MEDIUM, current_risk_class=RiskClass.MEDIUM, max_observed_risk_class=RiskClass.MEDIUM,
        execution_weight=weight,
    )


# --- select_context_strategy ---

def test_task_heavy_is_reproduction_first_impact_wide():
    assert select_context_strategy(ChangeKind.TASK, ExecutionWeight.HEAVY) == ContextStrategy.REPRODUCTION_FIRST_IMPACT_WIDE


def test_refactor_standard_is_target_module_direct_consumers():
    assert select_context_strategy(ChangeKind.REFACTOR, ExecutionWeight.STANDARD) == ContextStrategy.TARGET_MODULE_DIRECT_CONSUMERS


def test_milestone_standard_is_milestone_spec_and_contracts():
    assert select_context_strategy(ChangeKind.MILESTONE, ExecutionWeight.STANDARD) == ContextStrategy.MILESTONE_SPEC_AND_CONTRACTS


def test_unlisted_combination_falls_back_to_default_balanced():
    assert select_context_strategy(ChangeKind.ENHANCEMENT, ExecutionWeight.LIGHT) == ContextStrategy.DEFAULT_BALANCED


def test_strategy_selection_is_deterministic():
    for _ in range(5):
        assert select_context_strategy(ChangeKind.TASK, ExecutionWeight.HEAVY) == ContextStrategy.REPRODUCTION_FIRST_IMPACT_WIDE


# --- ContextOrchestrator.build() end to end ---

def test_build_includes_established_file_context_as_relevant_files():
    orchestrator = ContextOrchestrator()
    result = asyncio.run(orchestrator.build(
        request="fix the bug",
        route=_route(ChangeKind.TASK, ExecutionWeight.STANDARD),
        profile=STANDARD_PROFILE,
        workspace_path="/repo",
        milestone=None,
        control_state=ControlState.new(run_id="run-1"),
        established_file_context={"protocol.py": "class Protocol: pass"},
    ))
    assert len(result.relevant_files) == 1
    assert result.relevant_files[0].path == "protocol.py"
    assert result.relevant_files[0].source_type == "established_milestone_output"
    assert result.omitted == ()


def test_build_omits_established_files_that_exceed_the_token_budget():
    orchestrator = ContextOrchestrator()
    big_content = "x" * 100000  # ~25000 estimated tokens
    result = asyncio.run(orchestrator.build(
        request="fix the bug",
        route=_route(ChangeKind.TASK, ExecutionWeight.STANDARD),
        profile=STANDARD_PROFILE,
        workspace_path="/repo",
        milestone=None,
        control_state=ControlState.new(run_id="run-1"),
        established_file_context={"huge.py": big_content},
        token_budget=100,
    ))
    assert result.relevant_files == ()
    assert len(result.omitted) == 1
    assert result.omitted[0]["path"] == "huge.py"
    assert result.omitted[0]["estimated_tokens"] > 100


def test_build_wraps_raw_rag_context_in_baseline_not_relevant_files():
    orchestrator = ContextOrchestrator()
    result = asyncio.run(orchestrator.build(
        request="fix the bug",
        route=_route(ChangeKind.TASK, ExecutionWeight.STANDARD),
        profile=STANDARD_PROFILE,
        workspace_path="/repo",
        milestone=None,
        control_state=ControlState.new(run_id="run-1"),
        raw_rag_context="=== some retrieved code ===",
    ))
    assert result.baseline is not None
    assert result.baseline["rag_context"] == "=== some retrieved code ==="
    assert result.relevant_files == ()


def test_build_populates_spec_slice_from_milestone():
    milestone = MilestoneV2(
        id="M2", goal="Build the parser", mode=MilestoneMode.COMPOSITION,
        depends_on=["M1"], provides=[ProvidedCapability(name="Parser")], consumes=["ProtocolClient"],
        acceptance=[AcceptanceCriterion(id="A1", description="parses valid input")],
    )
    orchestrator = ContextOrchestrator()
    result = asyncio.run(orchestrator.build(
        request="build the parser",
        route=_route(ChangeKind.MILESTONE, ExecutionWeight.STANDARD),
        profile=STANDARD_PROFILE,
        workspace_path="/repo",
        milestone=milestone,
        control_state=ControlState.new(run_id="run-1"),
    ))
    assert result.spec_slice["id"] == "M2"
    assert result.spec_slice["goal"] == "Build the parser"
    assert result.spec_slice["depends_on"] == ["M1"]
    assert result.spec_slice["provides"] == ["Parser"]
    assert result.spec_slice["consumes"] == ["ProtocolClient"]
    assert result.spec_slice["acceptance"] == [{"id": "A1", "description": "parses valid input"}]


def test_build_carries_contract_and_artifact_entries_through_untouched():
    orchestrator = ContextOrchestrator()
    contract_entry = {"id": "M1:X", "state": "frozen"}
    artifact_entry = {"milestone_id": "M1", "ecosystem": "maven"}
    result = asyncio.run(orchestrator.build(
        request="x",
        route=_route(ChangeKind.MILESTONE, ExecutionWeight.STANDARD),
        profile=STANDARD_PROFILE,
        workspace_path="/repo",
        milestone=None,
        control_state=ControlState.new(run_id="run-1"),
        contract_entries=[contract_entry],
        artifact_entries=[artifact_entry],
    ))
    assert result.contract_entries == (contract_entry,)
    assert result.artifact_entries == (artifact_entry,)


def test_build_produces_a_real_package_hash_and_a_positive_token_count():
    orchestrator = ContextOrchestrator()
    result = asyncio.run(orchestrator.build(
        request="x", route=_route(ChangeKind.TASK, ExecutionWeight.LIGHT), profile=LIGHT_PROFILE,
        workspace_path="/repo", milestone=None, control_state=ControlState.new(run_id="run-1"),
        established_file_context={"a.py": "print(1)"},
    ))
    assert result.package_hash != ""
    assert result.token_count > 0


def test_build_records_the_selected_strategy_in_baseline():
    orchestrator = ContextOrchestrator()
    result = asyncio.run(orchestrator.build(
        request="x", route=_route(ChangeKind.TASK, ExecutionWeight.HEAVY), profile=HEAVY_PROFILE,
        workspace_path="/repo", milestone=None, control_state=ControlState.new(run_id="run-1"),
    ))
    assert result.baseline["strategy"] == ContextStrategy.REPRODUCTION_FIRST_IMPACT_WIDE.value
