"""PRD-032 defect (found by the live tiers): the structured plan schema
accepts only closed vocabularies, but the Planner prompt showed them only by
example. A real model invented verifier_kind "file_check" and the plan was
refused (planner_output_schema_invalid). The prompt now states every closed
vocabulary, derived from the schema's own enums, so the two cannot drift.
"""
from kriya.agents.agent import PlannerAgent
from kriya.workflow.plan_schema import ExecutionMethod, ExecutionRole, FileAction, VerificationMethodType, VerifierKind


def test_the_planner_prompt_states_every_closed_plan_vocabulary():
    prompt = PlannerAgent.system_prompt.fget(object.__new__(PlannerAgent))
    for label, enum in (("verification type", VerificationMethodType), ("verifier_kind", VerifierKind),
                        ("planned_files action", FileAction), ("execution_method", ExecutionMethod),
                        ("execution_role", ExecutionRole)):
        assert f"{label}: {' | '.join(member.value for member in enum)}" in prompt, label
    assert "workspace-relative" in prompt
