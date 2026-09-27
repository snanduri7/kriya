"""PRD-032 defect (found by the live chaos tier, L01/L02): the Planner and
Architect prompts embedded the repository model as JSON including
``root_path`` (the absolute host workspace path), while the structured plan
schema refuses any absolute planned path. The real model sometimes copied the
path, and Kriya then refused its own prompt's echo
(``planner_output_unauthorized_path``). Fixed: the prompt's workspace context
never carries the absolute workspace path; paths are workspace-relative
everywhere a model sees them.
"""
from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    benign_roles,
    chaos_config,
    chaos_engine,
    git_workspace,
    run_direct,
)


def test_no_planner_or_architect_prompt_carries_the_absolute_workspace_path(tmp_path):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    runtime = ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), "add sub to calc.py", workspace)
    assert result["quality_gates_passed"] is True
    planning = [request for request, role in zip(runtime.requests, runtime.roles, strict=True)
                if role in ("planner", "architect")]
    assert {role for role in runtime.roles if role in ("planner", "architect")} == {"planner", "architect"}
    for request in planning:
        text = "\n".join(str(message.get("content") or "") for message in request.messages)
        assert "Workspace Context:" in text
        assert str(workspace) not in text, "an absolute workspace path reached a planning prompt"
        assert '"root_path"' not in text
