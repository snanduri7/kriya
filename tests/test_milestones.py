import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.agents.contracts import Milestone, MilestoneList, parse_milestone_list
from kriya.workflow.milestones import (
    MilestoneRunState,
    build_integration_goal_text,
    build_milestone_goal_text,
    check_dependency_regression,
    load_milestone_run_state,
    load_or_resume_milestone_run_state,
    plan_milestones,
    render_established_file_context,
    replay_prior_milestone_verifications,
    run_milestones,
    save_milestone_run_state,
)


# ============================================================
# MilestoneList / parse_milestone_list contract
# ============================================================

def test_parse_milestone_list_extracts_valid_fenced_block():
    text = (
        "Here is my plan.\n```json\n"
        '{"milestones": [{"goal": "Start Ignite, stop cleanly", '
        '"success_criterion": "Prints PASS", "depends_on_previous": false}]}\n```'
    )
    milestones, err = parse_milestone_list(text)
    assert err is None
    assert len(milestones) == 1
    assert milestones[0].goal == "Start Ignite, stop cleanly"
    assert milestones[0].depends_on_previous is False


def test_parse_milestone_list_defaults_depends_on_previous_true():
    text = '```json\n{"milestones": [{"goal": "g", "success_criterion": "c"}]}\n```'
    milestones, err = parse_milestone_list(text)
    assert err is None
    assert milestones[0].depends_on_previous is True


def test_parse_milestone_list_returns_error_on_no_json_block():
    milestones, err = parse_milestone_list("just prose, no json anywhere")
    assert milestones is None
    assert err is not None


def test_parse_milestone_list_returns_error_on_empty_text():
    milestones, err = parse_milestone_list("")
    assert milestones is None
    assert err == "text is empty"


def test_parse_milestone_list_bare_object_survives_a_bracket_in_goal_text():
    """Regression guard for a real finding: the no-fence fallback regex used
    to be non-greedy and truncated at the FIRST ']' anywhere in the text -
    including one inside a milestone's own goal/success_criterion (e.g. an
    array-index reference, or a bracketed marker like this project's own
    "[VERIFICATION] PASS" convention), breaking json.loads on well-formed
    JSON. No fence at all here (this fallback path is only reached when the
    model doesn't wrap output in a code fence)."""
    text = (
        '{"milestones": [{"goal": "Return items[0] from the list", '
        '"success_criterion": "Prints items[0]", "depends_on_previous": false}, '
        '{"goal": "g2", "success_criterion": "c2"}]}'
    )
    milestones, err = parse_milestone_list(text)
    assert err is None
    assert len(milestones) == 2
    assert milestones[0].goal == "Return items[0] from the list"


def test_milestone_list_rejects_empty_list():
    with pytest.raises(Exception):
        MilestoneList(milestones=[])


def test_milestone_list_rejects_more_than_eight_milestones():
    with pytest.raises(Exception):
        MilestoneList(milestones=[Milestone(goal=f"g{i}", success_criterion=f"c{i}") for i in range(9)])


def test_milestone_rejects_blank_goal_or_criterion():
    with pytest.raises(Exception):
        Milestone(goal="   ", success_criterion="c")
    with pytest.raises(Exception):
        Milestone(goal="g", success_criterion="")


# ============================================================
# MilestonePlannerAgent
# ============================================================

@pytest.mark.asyncio
async def test_milestone_planner_agent_run_with_milestone_list():
    from kriya.agents.agent import MilestonePlannerAgent
    from kriya.config import AppConfig
    from kriya.core.llm import LLMClient

    llm = LLMClient(AppConfig())
    agent = MilestonePlannerAgent("milestone_planner", llm)
    llm.complete = AsyncMock(
        return_value='```json\n{"milestones": [{"goal": "g", "success_criterion": "c", "depends_on_previous": false}]}\n```'
    )
    raw, milestones = await agent.run_with_milestone_list("prompt")
    assert milestones is not None
    assert milestones[0].goal == "g"


@pytest.mark.asyncio
async def test_milestone_planner_agent_returns_none_on_malformed_output():
    from kriya.agents.agent import MilestonePlannerAgent
    from kriya.config import AppConfig
    from kriya.core.llm import LLMClient

    llm = LLMClient(AppConfig())
    agent = MilestonePlannerAgent("milestone_planner", llm)
    llm.complete = AsyncMock(return_value="no json here at all")
    _raw, milestones = await agent.run_with_milestone_list("prompt")
    assert milestones is None


def test_milestone_planner_agent_prompt_forbids_multiple_build_artifacts():
    """Regression guard: this agent's prompt must explicitly forbid the same
    multi-module anti-pattern PlannerAgent's own MINIMALISM instruction was
    added to prevent, one level up (across milestone boundaries instead of
    within one goal)."""
    from kriya.agents.agent import MilestonePlannerAgent
    from kriya.config import AppConfig
    from kriya.core.llm import LLMClient

    agent = MilestonePlannerAgent("milestone_planner", LLMClient(AppConfig()))
    prompt = agent.system_prompt
    assert "DO NOT PROPOSE MULTIPLE BUILD ARTIFACTS" in prompt
    assert "SLICE BY BEHAVIOR, NOT BY STRUCTURE" in prompt


# ============================================================
# Pure string-assembly functions
# ============================================================

def test_build_milestone_goal_text_first_milestone_has_no_prior_work_header():
    """depends_on_previous=False (milestone 1's expected value) must NOT claim
    prior milestones already exist - there aren't any yet. Regression guard
    for a real finding: the header used to be unconditional, so milestone 1's
    own goal text falsely told the model earlier work already existed."""
    m = Milestone(goal="Start Ignite, stop cleanly", success_criterion="Prints PASS", depends_on_previous=False)
    text = build_milestone_goal_text(m, 1, 3, [])
    assert "Prior milestones have already been applied" not in text
    assert "milestone 1 of 3" not in text
    assert "Start Ignite, stop cleanly" in text
    assert "Verification: Prints PASS" in text
    assert "Dependencies established" not in text


def test_build_milestone_goal_text_first_milestone_ignores_depends_on_previous_true():
    """Milestone.depends_on_previous defaults to True (kriya/agents/contracts.py) -
    a model that simply omits the field for milestone 1 in its JSON output must
    NOT get the "prior milestones already exist, do NOT recreate/restructure
    anything" header on a brand-new, empty workspace. index == 1 structurally
    has no predecessor, regardless of what depends_on_previous says."""
    m = Milestone(goal="Start Ignite, stop cleanly", success_criterion="Prints PASS", depends_on_previous=True)
    text = build_milestone_goal_text(m, 1, 3, [])
    assert "Prior milestones have already been applied" not in text
    assert "milestone 1 of 3" not in text


def test_build_milestone_goal_text_dependent_milestone_gets_prior_work_header():
    m = Milestone(goal="Add caching", success_criterion="Object round-trips", depends_on_previous=True)
    text = build_milestone_goal_text(m, 2, 3, ["org.apache.ignite:ignite-core"])
    assert "milestone 2 of 3" in text
    assert "Prior milestones have already been applied" in text
    assert "Dependencies established by earlier milestones" in text
    assert "org.apache.ignite:ignite-core" in text


def test_build_integration_goal_text_synthesizes_from_success_criteria():
    milestones = [
        Milestone(goal="g1", success_criterion="c1", depends_on_previous=False),
        Milestone(goal="g2", success_criterion="c2"),
    ]
    text = build_integration_goal_text("original goal text", milestones)
    assert "original goal text" in text
    assert "1. c1" in text
    assert "2. c2" in text
    assert "Do NOT rewrite, restructure" in text
    # The original goal's OWN prose must not be what gets re-submitted as the
    # milestone success criteria - only the goal text itself appears once,
    # under its own labeled section.
    assert text.count("original goal text") == 1


# ============================================================
# render_established_file_context - goes to supplementary_context, NOT
# build_milestone_goal_text's own goal text (see run_generation_workflow's
# docstring: Architect only ever sees Planner's `plan` output again, never
# the raw goal, so grounding folded into goal text alone would silently be
# lost the moment Planner doesn't transcribe it into its own plan).
# ============================================================

def test_render_established_file_context_empty_is_blank():
    assert render_established_file_context({}) == ""


def test_render_established_file_context_includes_real_content_sorted():
    text = render_established_file_context({
        "src/main/java/Protocol.java": "public Protocol(byte f1, short f2) { ... }",
        "src/main/java/AnotherFile.java": "class AnotherFile {}",
    })
    assert "src/main/java/Protocol.java" in text
    assert "public Protocol(byte f1, short f2)" in text
    assert "src/main/java/AnotherFile.java" in text
    assert "already built by an earlier milestone" in text
    # Deterministic ordering, not dict insertion order.
    assert text.index("AnotherFile.java") < text.index("Protocol.java")


# ============================================================
# MilestoneRunState persistence
# ============================================================

def test_milestone_run_state_round_trips_through_sidecar_file():
    milestones = [Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)]
    state = MilestoneRunState(
        group_id="grp-1", original_goal="orig", milestones=milestones,
        completed_milestone_indices=[1], established_dependencies=["dep:a"],
        verification_commands={1: [["mvn", "exec:exec"]]},
        established_file_context={"src/Foo.java": "class Foo { void bar(); }"},
    )
    with tempfile.TemporaryDirectory() as tmp:
        save_milestone_run_state(tmp, state)
        loaded = load_milestone_run_state(tmp, "grp-1")
        assert loaded.group_id == "grp-1"
        assert loaded.milestones[0].goal == "g1"
        assert loaded.completed_milestone_indices == [1]
        assert loaded.established_dependencies == ["dep:a"]
        assert loaded.verification_commands == {1: [["mvn", "exec:exec"]]}
        assert loaded.established_file_context == {"src/Foo.java": "class Foo { void bar(); }"}


def test_load_milestone_run_state_returns_none_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        assert load_milestone_run_state(tmp, "nonexistent") is None


def test_load_or_resume_prefers_fresh_plan_milestones_but_resumes_progress():
    """Regression guard for a real finding: `generate --from-milestones` used
    to prefer a stale sidecar wholesale once ANY milestone had completed,
    silently discarding a hand-edit made to the plan file afterward - even
    though `plan-milestones` explicitly advertises hand-editing as the
    intended workflow. Editing the plan file between runs must take effect;
    resume progress must still be preserved."""
    original_milestones = [
        Milestone(goal="original goal 1", success_criterion="c1", depends_on_previous=False),
        Milestone(goal="original goal 2", success_criterion="c2"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        sidecar_state = MilestoneRunState(
            group_id="grp", original_goal="orig", milestones=original_milestones,
            completed_milestone_indices=[1], established_dependencies=["dep:a"],
            verification_commands={1: [["echo", "ok"]]},
            established_file_context={"src/Foo.java": "class Foo {}"},
        )
        save_milestone_run_state(tmp, sidecar_state)

        edited_milestones = [
            Milestone(goal="original goal 1", success_criterion="c1", depends_on_previous=False),
            Milestone(goal="EDITED goal 2", success_criterion="c2-edited"),
        ]
        plan_data = MilestoneRunState(
            group_id="grp", original_goal="orig", milestones=edited_milestones
        ).to_dict()

        merged = load_or_resume_milestone_run_state(tmp, plan_data)

    assert merged.milestones[1].goal == "EDITED goal 2"  # hand-edit honored
    assert merged.completed_milestone_indices == [1]  # progress preserved
    assert merged.established_dependencies == ["dep:a"]
    assert merged.verification_commands == {1: [["echo", "ok"]]}
    assert merged.established_file_context == {"src/Foo.java": "class Foo {}"}


def test_load_or_resume_with_no_existing_sidecar_starts_fresh():
    milestones = [Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)]
    plan_data = MilestoneRunState(group_id="grp-new", original_goal="orig", milestones=milestones).to_dict()
    with tempfile.TemporaryDirectory() as tmp:
        merged = load_or_resume_milestone_run_state(tmp, plan_data)
    assert merged.completed_milestone_indices == []
    assert merged.milestones[0].goal == "g1"


# ============================================================
# Dependency-drop guard
# ============================================================

def test_check_dependency_regression_detects_a_real_drop():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "pom.xml"), "w") as f:
            f.write("<project></project>")
        with patch("kriya.tools.validate.get_pom_dependencies", return_value=["a:1", "b:1"]):
            dropped = check_dependency_regression(tmp, ["a:1", "b:1", "c:1"])
        assert dropped == ["c:1"]


def test_check_dependency_regression_no_drop_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "pom.xml"), "w") as f:
            f.write("<project></project>")
        with patch("kriya.tools.validate.get_pom_dependencies", return_value=["a:1", "b:1"]):
            assert check_dependency_regression(tmp, ["a:1"]) == []


def test_check_dependency_regression_skips_when_no_established_deps_yet():
    with tempfile.TemporaryDirectory() as tmp:
        assert check_dependency_regression(tmp, []) == []


def test_check_dependency_regression_skips_when_no_pom_xml():
    with tempfile.TemporaryDirectory() as tmp:
        assert check_dependency_regression(tmp, ["a:1"]) == []


# ============================================================
# plan_milestones
# ============================================================

@pytest.mark.asyncio
async def test_plan_milestones_success():
    planner = MagicMock()
    milestones = [Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)]
    planner.run_with_milestone_list = AsyncMock(return_value=("raw", milestones))
    with tempfile.TemporaryDirectory() as tmp:
        state, err = await plan_milestones(planner, "big goal", tmp)
    assert err is None
    assert state.original_goal == "big goal"
    assert state.milestones == milestones
    assert state.group_id


@pytest.mark.asyncio
async def test_plan_milestones_failure_on_invalid_output():
    planner = MagicMock()
    planner.run_with_milestone_list = AsyncMock(return_value=("raw", None))
    with tempfile.TemporaryDirectory() as tmp:
        state, err = await plan_milestones(planner, "goal", tmp)
    assert state is None
    assert err is not None


# ============================================================
# run_milestones control flow
# ============================================================

@pytest.mark.asyncio
async def test_run_milestones_success_path_through_all_milestones_and_integration():
    milestones = [
        Milestone(goal="g1", success_criterion="c1", depends_on_previous=False),
        Milestone(goal="g2", success_criterion="c2"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=milestones)
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(
            return_value={"quality_gates_passed": True, "design": "d", "files": ["a.py"]}
        )
        we.run_verifier = MagicMock()
        we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})

        result = await run_milestones(we, state, tmp)

    assert result["status"] == "success"
    assert we.run_generation_workflow.await_count == 3  # 2 milestones + integration
    assert state.completed_milestone_indices == [1, 2]


@pytest.mark.asyncio
async def test_run_milestones_grounds_later_milestones_on_earlier_ones_real_files():
    """Regression guard for a real, live-validation-confirmed finding
    (2026-08-21, protocol-encoder milestone run): milestone N's Architect
    invented a fictional API ("I'll assume there's an existing protocol
    class that has encode/decode methods") for a class an EARLIER milestone
    had already built with a different, real signature, because nothing
    grounded it on the real file - Graph RAG's own relevance scoring didn't
    surface it. Milestone 1 writes a real file to the workspace; milestone
    2's run_generation_workflow() call must receive that file's real content
    via supplementary_context, and run_state.established_file_context must
    be populated from it."""
    milestones = [
        Milestone(goal="Build Protocol class", success_criterion="c1", depends_on_previous=False),
        Milestone(goal="Write a main class using Protocol", success_criterion="c2"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=milestones)
        we = MagicMock()

        real_signature = "public Protocol(byte f1, short f2, int f3, long f4, byte[] p) { ... }"
        os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
        with open(os.path.join(tmp, "src", "Protocol.java"), "w", encoding="utf-8") as fh:
            fh.write(real_signature)

        we.run_generation_workflow = AsyncMock(
            return_value={"quality_gates_passed": True, "design": "d", "files": ["src/Protocol.java"]}
        )
        we.run_verifier = MagicMock()
        we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})

        await run_milestones(we, state, tmp)

    assert state.established_file_context["src/Protocol.java"] == real_signature

    # Milestone 1's own call had nothing established yet.
    first_call_kwargs = we.run_generation_workflow.await_args_list[0].kwargs
    assert first_call_kwargs["supplementary_context"] == ""

    # Milestone 2's call must carry milestone 1's real file content forward.
    second_call_kwargs = we.run_generation_workflow.await_args_list[1].kwargs
    assert real_signature in second_call_kwargs["supplementary_context"]
    assert "src/Protocol.java" in second_call_kwargs["supplementary_context"]

    # The integration pass gets it too.
    integration_call_kwargs = we.run_generation_workflow.await_args_list[2].kwargs
    assert real_signature in integration_call_kwargs["supplementary_context"]


@pytest.mark.asyncio
async def test_run_milestones_passes_pom_content_to_judge_for_exec_shape_inference():
    """Regression guard for a real finding: RunVerifierAgent.judge() reliably
    mis-infers exec:java for a pom actually shaped for exec:exec when called
    without build_file_content (confirmed, documented in judge()'s own
    prompt/docstring) - exactly the goal shape (Ignite/Qpid, needing
    --add-opens) this whole feature was built around. Must read and pass the
    real, current pom.xml, same convention as attempt.py's own call site."""
    milestones = [Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)]
    with tempfile.TemporaryDirectory() as tmp:
        pom_content = "<project><build><plugins>exec-maven-plugin</plugins></build></project>"
        with open(os.path.join(tmp, "pom.xml"), "w") as f:
            f.write(pom_content)
        state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=milestones)
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(
            return_value={"quality_gates_passed": True, "design": "d", "files": []}
        )
        we.run_verifier = MagicMock()
        we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})

        await run_milestones(we, state, tmp)

    we.run_verifier.judge.assert_called_once()
    assert we.run_verifier.judge.call_args.kwargs["build_file_content"] == pom_content


@pytest.mark.asyncio
async def test_run_milestones_judge_build_file_content_none_without_pom():
    milestones = [Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)]
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=milestones)
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(
            return_value={"quality_gates_passed": True, "design": "d", "files": []}
        )
        we.run_verifier = MagicMock()
        we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})

        await run_milestones(we, state, tmp)

    assert we.run_verifier.judge.call_args.kwargs["build_file_content"] is None


@pytest.mark.asyncio
async def test_run_milestones_failure_defaults_to_abandon_with_no_callback():
    milestones = [Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)]
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=milestones)
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": False})
        result = await run_milestones(we, state, tmp)
    assert result["status"] == "milestone_failed"
    assert result["milestone_index"] == 1
    assert we.run_generation_workflow.await_count == 1


@pytest.mark.asyncio
async def test_run_milestones_failure_callback_abandon_stops_sequence():
    milestones = [
        Milestone(goal="g1", success_criterion="c1", depends_on_previous=False),
        Milestone(goal="g2", success_criterion="c2"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=milestones)
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": False})
        cb = MagicMock(return_value="abandon")

        result = await run_milestones(we, state, tmp, milestone_failure_callback=cb)

    assert result["status"] == "milestone_failed"
    cb.assert_called_once()
    assert we.run_generation_workflow.await_count == 1  # never reached milestone 2


@pytest.mark.asyncio
async def test_run_milestones_failure_callback_retry_then_succeeds():
    milestones = [Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)]
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=milestones)
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(side_effect=[
            {"quality_gates_passed": False},
            {"quality_gates_passed": True, "design": "d", "files": []},
            {"quality_gates_passed": True},
        ])
        we.run_verifier = MagicMock()
        we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
        cb = MagicMock(return_value="retry")

        result = await run_milestones(we, state, tmp, milestone_failure_callback=cb)

    assert result["status"] == "success"
    assert we.run_generation_workflow.await_count == 3
    cb.assert_called_once()


@pytest.mark.asyncio
async def test_run_milestones_dependency_regression_routes_through_failure_callback():
    """Regression guard for a real finding: a dependency drop used to raise
    straight past milestone_failure_callback (bypassing the same human
    decision point every other milestone failure goes through) even though
    the milestone's files were already applied to the real workspace by that
    point. Must now be reported to the SAME callback, not an uncaught
    exception."""
    milestones = [Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)]
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "pom.xml"), "w") as f:
            f.write("<project></project>")
        state = MilestoneRunState(
            group_id="grp", original_goal="orig", milestones=milestones,
            established_dependencies=["org.apache.ignite:ignite-core"],
        )
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(
            return_value={"quality_gates_passed": True, "design": "d", "files": []}
        )
        cb = MagicMock(return_value="abandon")
        with patch("kriya.tools.validate.get_pom_dependencies", return_value=["some.other:dep"]):
            result = await run_milestones(we, state, tmp, milestone_failure_callback=cb)

    assert result["status"] == "milestone_failed"
    cb.assert_called_once()
    failure_result = cb.call_args[0][3]
    assert failure_result["status"] == "dependency_regression"
    assert "org.apache.ignite:ignite-core" in failure_result["dropped_dependencies"]
    assert failure_result["quality_gates_passed"] is False
    # Not marked complete - a re-run of the same plan would retry this
    # milestone, not silently skip past a workspace with a dropped dependency.
    assert state.completed_milestone_indices == []


@pytest.mark.asyncio
async def test_run_milestones_dependency_regression_retry_recovers():
    milestones = [Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)]
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "pom.xml"), "w") as f:
            f.write("<project></project>")
        state = MilestoneRunState(
            group_id="grp", original_goal="orig", milestones=milestones,
            established_dependencies=["org.apache.ignite:ignite-core"],
        )
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(
            return_value={"quality_gates_passed": True, "design": "d", "files": []}
        )
        we.run_verifier = MagicMock()
        we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
        cb = MagicMock(return_value="retry")
        # 1st call: the regression check on the first attempt (reports a
        # drop). 2nd call: the regression check on the retried attempt
        # (clean). 3rd call: the post-loop "refresh established_dependencies"
        # read, same clean result.
        clean = ["org.apache.ignite:ignite-core", "some.other:dep"]
        with patch(
            "kriya.tools.validate.get_pom_dependencies",
            side_effect=[["some.other:dep"], clean, clean],
        ):
            result = await run_milestones(we, state, tmp, milestone_failure_callback=cb)

    assert result["status"] == "success"
    cb.assert_called_once()
    assert state.completed_milestone_indices == [1]


@pytest.mark.asyncio
async def test_run_milestones_resume_skips_already_completed():
    milestones = [
        Milestone(goal="g1", success_criterion="c1", depends_on_previous=False),
        Milestone(goal="g2", success_criterion="c2"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(
            group_id="grp", original_goal="orig", milestones=milestones,
            completed_milestone_indices=[1],
        )
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(
            return_value={"quality_gates_passed": True, "design": "d", "files": []}
        )
        we.run_verifier = MagicMock()
        we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})

        result = await run_milestones(we, state, tmp)

    assert result["status"] == "success"
    assert we.run_generation_workflow.await_count == 2  # milestone 2 + integration only


# ============================================================
# Replay mechanism (integration-phase regression detection)
# ============================================================

def test_replay_catches_a_real_regression():
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(
            group_id="grp", original_goal="orig",
            milestones=[Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)],
            verification_commands={1: [["echo", "hi"]]},
        )
        mock_validator = MagicMock()
        mock_validator.run_app_sequence.return_value = {
            "timed_out": False, "output": "[VERIFICATION] FAIL: something broke",
        }
        with patch("kriya.tools.validate.PolymorphicValidator", return_value=mock_validator), \
             patch("kriya.workflow.milestones._resolve_run_command", side_effect=lambda c, w: c):
            failures = replay_prior_milestone_verifications(tmp, state)
    assert len(failures) == 1
    assert failures[0]["milestone_index"] == 1
    assert "something broke" in failures[0]["reason"]


def test_replay_no_marker_is_not_treated_as_failure():
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(
            group_id="grp", original_goal="orig",
            milestones=[Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)],
            verification_commands={1: [["echo", "hi"]]},
        )
        mock_validator = MagicMock()
        mock_validator.run_app_sequence.return_value = {"timed_out": False, "output": "no marker here"}
        with patch("kriya.tools.validate.PolymorphicValidator", return_value=mock_validator), \
             patch("kriya.workflow.milestones._resolve_run_command", side_effect=lambda c, w: c):
            failures = replay_prior_milestone_verifications(tmp, state)
    assert failures == []


def test_replay_timeout_is_reported_as_a_failure():
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(
            group_id="grp", original_goal="orig",
            milestones=[Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)],
            verification_commands={1: [["sleep", "999"]]},
        )
        mock_validator = MagicMock()
        mock_validator.run_app_sequence.return_value = {"timed_out": True, "output": ""}
        with patch("kriya.tools.validate.PolymorphicValidator", return_value=mock_validator), \
             patch("kriya.workflow.milestones._resolve_run_command", side_effect=lambda c, w: c):
            failures = replay_prior_milestone_verifications(tmp, state)
    assert len(failures) == 1
    assert failures[0]["milestone_index"] == 1
    assert "timed out" in failures[0]["reason"]


@pytest.mark.asyncio
async def test_run_milestones_stops_before_integration_call_on_replay_failure():
    milestones = [Milestone(goal="g1", success_criterion="c1", depends_on_previous=False)]
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=milestones)
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(
            return_value={"quality_gates_passed": True, "design": "d", "files": []}
        )
        we.run_verifier = MagicMock()
        we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
        with patch(
            "kriya.workflow.milestones.replay_prior_milestone_verifications",
            return_value=[{"milestone_index": 1, "reason": "regressed"}],
        ):
            result = await run_milestones(we, state, tmp)

    assert result["status"] == "milestone_replay_failed"
    assert result["failures"][0]["milestone_index"] == 1
    assert we.run_generation_workflow.await_count == 1  # integration call never fired


# ============================================================
# traces.db additive schema
# ============================================================

def test_trace_logger_persists_milestone_columns():
    from kriya.core.trace import TraceLogger
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "traces.db")
        t = TraceLogger(db_path)
        t.log_run(run_id="r1", goal="g", duration_sec=1.0, attempts=0, status="success", files_modified=[])
        t.log_run(
            run_id="r2", goal="g2", duration_sec=2.0, attempts=1, status="success", files_modified=[],
            milestone_group_id="grp1", milestone_index=1, milestone_total=3,
        )
        t.close()

        conn = sqlite3.connect(db_path)
        rows = {
            r[0]: r[1:]
            for r in conn.execute(
                "SELECT run_id, milestone_group_id, milestone_index, milestone_total FROM runs"
            ).fetchall()
        }
        conn.close()

    assert rows["r1"] == (None, None, None)
    assert rows["r2"] == ("grp1", 1, 3)


def test_trace_logger_schema_migration_is_idempotent():
    """Re-initializing an already-migrated traces.db (ALTER TABLE ADD COLUMN
    on columns that already exist) must not raise - same pattern as the
    existing failure_category column."""
    from kriya.core.trace import TraceLogger

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "traces.db")
        TraceLogger(db_path).close()
        TraceLogger(db_path).close()  # second init must not raise
