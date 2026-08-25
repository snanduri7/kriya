import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.agents.contracts import (
    AcceptanceCriterion,
    Milestone,
    MilestoneList,
    MilestoneMode,
    MilestoneV2,
    ProvidedCapability,
    parse_milestone_list,
)
from kriya.control.contracts import ContractState
from kriya.control.persistence import (
    load_contract_registry,
    load_control_state,
    save_control_state,
)
from kriya.control.state import CURRENT_SCHEMA_VERSION, ControlState
from kriya.workflow.checkpoint import checkpoint_path, save_checkpoint
from kriya.workflow.milestones import (
    MilestoneRunState,
    build_integration_goal_text,
    build_milestone_goal_text,
    check_dependency_regression,
    load_milestone_run_state,
    load_or_resume_milestone_run_state,
    plan_milestones,
    render_established_file_context,
    render_repository_topology_summary,
    replay_prior_milestone_verifications,
    run_milestones,
    save_milestone_run_state,
)
from kriya.workflow.repository_topology import RepositoryTopology


def mkv2(id, goal="g", success_criterion="c", depends_on=None, mode=None, extends=None, provides=None, consumes=None):
    """MilestoneV2 test helper - `success_criterion` becomes the milestone's
    single acceptance criterion, mirroring how normalize_legacy_milestones()
    (kriya/workflow/milestone_normalization.py) maps a v1 milestone's own
    single success_criterion, so most MA3.6/pre-MA3.7 test bodies below only
    needed their `Milestone(...)` constructor calls swapped for this one.
    `provides`, when given, is a list of {"name": ..., "description": ...}
    dicts (description optional) - kept as plain dicts here rather than
    requiring every caller to import ProvidedCapability directly."""
    return MilestoneV2(
        id=id, goal=goal, depends_on=depends_on or [], mode=mode, extends=extends,
        acceptance=[AcceptanceCriterion(id=f"{id}-A1", description=success_criterion)],
        provides=[ProvidedCapability(**p) for p in (provides or [])],
        consumes=list(consumes or []),
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
async def test_milestone_planner_agent_run_with_milestone_list_parses_v2_directly():
    """MA3.7: the agent's real, live JSON contract is Schema v2 now - a
    well-behaved model's response parses straight into MilestoneV2 without
    ever touching the v1 fallback path."""
    from kriya.agents.agent import MilestonePlannerAgent
    from kriya.config import AppConfig
    from kriya.core.llm import LLMClient

    llm = LLMClient(AppConfig())
    agent = MilestonePlannerAgent("milestone_planner", llm)
    llm.complete = AsyncMock(
        return_value='```json\n{"milestones": [{"id": "M1", "goal": "g", "depends_on": [], '
        '"acceptance": [{"id": "M1-A1", "description": "c"}]}]}\n```'
    )
    raw, milestones = await agent.run_with_milestone_list("prompt")
    assert milestones is not None
    assert isinstance(milestones[0], MilestoneV2)
    assert milestones[0].id == "M1"
    assert milestones[0].goal == "g"
    assert milestones[0].acceptance[0].description == "c"


@pytest.mark.asyncio
async def test_milestone_planner_agent_falls_back_to_v1_and_normalizes():
    """A smaller local model reverting to the older, longer-established v1
    shape despite the new v2 prompt is a real, expected risk - the same
    reasoning behind the batch-JSON/iterative-per-file fallbacks already
    established elsewhere in this codebase. Must still produce a usable
    MilestoneV2 list, not fail decomposition outright."""
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
    assert isinstance(milestones[0], MilestoneV2)
    assert milestones[0].id == "M1"
    assert milestones[0].goal == "g"
    assert milestones[0].acceptance[0].description == "c"


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


def test_milestone_planner_agent_prompt_preserves_physical_topology_by_default():
    """MA3.5 regression guard: this agent's prompt no longer carries the
    absolute "DO NOT PROPOSE MULTIPLE BUILD ARTIFACTS" ban (the deterministic
    MilestonePlanValidator physical-topology-preservation check,
    kriya/workflow/milestone_validation.py, is the authoritative gate now -
    see that module's own MA3.4 docstring for why the replacement had to
    exist BEFORE this prompt was loosened), but it must still preserve the
    same underlying principle in nuanced form: milestone boundaries are not
    build boundaries, and the same-project default (EXTENSION) still needs
    explicit justification (repository evidence or an explicit goal) to be
    overridden by a genuinely new build artifact (COMPOSITION allowing one)."""
    from kriya.agents.agent import MilestonePlannerAgent
    from kriya.config import AppConfig
    from kriya.core.llm import LLMClient

    agent = MilestonePlannerAgent("milestone_planner", LLMClient(AppConfig()))
    prompt = agent.system_prompt
    assert "DO NOT PROPOSE MULTIPLE BUILD ARTIFACTS" not in prompt
    assert "MILESTONE BOUNDARIES ARE NOT BUILD BOUNDARIES" in prompt
    assert "EXTENSION" in prompt
    assert "COMPOSITION" in prompt
    assert "SLICE BY BEHAVIOR, NOT BY STRUCTURE" in prompt
    # JSON output contract is Schema v2 as of MA3.7 (MilestoneListV2/
    # parse_milestone_list_v2) - id/depends_on/acceptance, not the old v1
    # success_criterion/depends_on_previous shape.
    assert '"depends_on"' in prompt
    assert '"acceptance"' in prompt
    assert '"depends_on_previous"' not in prompt


# ============================================================
# Pure string-assembly functions
# ============================================================

def test_build_milestone_goal_text_root_milestone_has_no_prior_work_header():
    """A milestone with an EMPTY depends_on (a real DAG root, MA3.7) must NOT
    claim prior milestones already exist - there aren't any it depends on.
    Regression guard for the original real finding this test predates: the
    header used to be unconditional, so milestone 1's own goal text falsely
    told the model earlier work already existed."""
    m = mkv2("M1", goal="Start Ignite, stop cleanly", success_criterion="Prints PASS")
    text = build_milestone_goal_text(m, 1, 3, [])
    assert "Prior milestones have already been applied" not in text
    assert "depending on:" not in text
    assert "Start Ignite, stop cleanly" in text
    assert "Verification: Prints PASS" in text
    assert "Dependencies established" not in text


def test_build_milestone_goal_text_dependent_milestone_gets_prior_work_header():
    m = mkv2("M2", goal="Add caching", success_criterion="Object round-trips", depends_on=["M1"])
    text = build_milestone_goal_text(m, 2, 3, ["org.apache.ignite:ignite-core"])
    assert "depending on: M1" in text
    assert "Prior milestones have already been applied" in text
    assert "Dependencies established by earlier milestones" in text
    assert "org.apache.ignite:ignite-core" in text


def test_build_milestone_goal_text_extension_names_what_it_extends():
    m = mkv2("M2", goal="Add caching", success_criterion="ok", depends_on=["M1"], mode=MilestoneMode.EXTENSION, extends="M1")
    text = build_milestone_goal_text(m, 2, 2, [])
    assert "EXTENDS milestone 'M1'" in text


def test_build_integration_goal_text_synthesizes_from_acceptance_criteria():
    milestones = [
        mkv2("M1", goal="g1", success_criterion="c1"),
        mkv2("M2", goal="g2", success_criterion="c2", depends_on=["M1"]),
    ]
    text = build_integration_goal_text("original goal text", milestones)
    assert "original goal text" in text
    assert "M1: c1" in text
    assert "M2: c2" in text
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


def test_render_established_file_context_calls_out_build_layout_not_just_signatures():
    """Regression test for the established-files audit (2026-08-22): tracing
    why the Architect still picked a Maven-conventional package for a new
    file while an established file sat in the default package - despite the
    established file's real content already being in front of it via this
    exact function - found that the directive text only ever told the model
    to match signatures, never package/directory layout. Confirms the fix:
    the block now says so explicitly, not just "match signatures"."""
    text = render_established_file_context({"Protocol.java": "public class Protocol {}"})
    assert "package" in text
    assert "default" in text or "unnamed package" in text
    assert "constructor/method signatures" in text


# ============================================================
# MilestoneRunState persistence
# ============================================================

def test_milestone_run_state_round_trips_through_sidecar_file():
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
    state = MilestoneRunState(
        group_id="grp-1", original_goal="orig", milestones=milestones,
        completed_milestone_ids=["M1"], established_dependencies=["dep:a"],
        verification_commands={"M1": [["mvn", "exec:exec"]]},
        established_file_context={"src/Foo.java": "class Foo { void bar(); }"},
    )
    with tempfile.TemporaryDirectory() as tmp:
        save_milestone_run_state(tmp, state)
        loaded = load_milestone_run_state(tmp, "grp-1")
        assert loaded.group_id == "grp-1"
        assert loaded.milestones[0].goal == "g1"
        assert loaded.completed_milestone_ids == ["M1"]
        assert loaded.established_dependencies == ["dep:a"]
        assert loaded.verification_commands == {"M1": [["mvn", "exec:exec"]]}
        assert loaded.established_file_context == {"src/Foo.java": "class Foo { void bar(); }"}


def test_milestone_run_state_loads_a_legacy_v1_sidecar_file():
    """Rule 9: legacy (pre-MA3.7) milestone plans remain loadable. A saved v1
    sidecar has no "id" field and int-keyed progress - both must migrate to
    their v2/id-based equivalents transparently."""
    legacy = {
        "group_id": "grp-legacy", "original_goal": "orig",
        "milestones": [
            {"goal": "g1", "success_criterion": "c1", "depends_on_previous": False},
            {"goal": "g2", "success_criterion": "c2", "depends_on_previous": True},
        ],
        "completed_milestone_indices": [1],
        "established_dependencies": ["dep:a"],
        "verification_commands": {"1": [["mvn", "exec:exec"]]},
        "established_file_context": {"src/Foo.java": "class Foo {}"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, ".kriya", "milestones", "grp-legacy.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import json as _json
        with open(path, "w") as f:
            _json.dump(legacy, f)
        loaded = load_milestone_run_state(tmp, "grp-legacy")
    assert loaded.milestones[0].id == "M1" and loaded.milestones[1].id == "M2"
    assert loaded.milestones[1].depends_on == ["M1"]
    assert loaded.completed_milestone_ids == ["M1"]
    assert loaded.verification_commands == {"M1": [["mvn", "exec:exec"]]}


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
        mkv2("M1", goal="original goal 1", success_criterion="c1"),
        mkv2("M2", goal="original goal 2", success_criterion="c2", depends_on=["M1"]),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        sidecar_state = MilestoneRunState(
            group_id="grp", original_goal="orig", milestones=original_milestones,
            completed_milestone_ids=["M1"], established_dependencies=["dep:a"],
            verification_commands={"M1": [["echo", "ok"]]},
            established_file_context={"src/Foo.java": "class Foo {}"},
        )
        save_milestone_run_state(tmp, sidecar_state)

        edited_milestones = [
            mkv2("M1", goal="original goal 1", success_criterion="c1"),
            mkv2("M2", goal="EDITED goal 2", success_criterion="c2-edited", depends_on=["M1"]),
        ]
        plan_data = MilestoneRunState(
            group_id="grp", original_goal="orig", milestones=edited_milestones
        ).to_dict()

        merged = load_or_resume_milestone_run_state(tmp, plan_data)

    assert merged.milestones[1].goal == "EDITED goal 2"  # hand-edit honored
    assert merged.completed_milestone_ids == ["M1"]  # progress preserved
    assert merged.established_dependencies == ["dep:a"]
    assert merged.verification_commands == {"M1": [["echo", "ok"]]}
    assert merged.established_file_context == {"src/Foo.java": "class Foo {}"}


def test_load_or_resume_with_no_existing_sidecar_starts_fresh():
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
    plan_data = MilestoneRunState(group_id="grp-new", original_goal="orig", milestones=milestones).to_dict()
    with tempfile.TemporaryDirectory() as tmp:
        merged = load_or_resume_milestone_run_state(tmp, plan_data)
    assert merged.completed_milestone_ids == []
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
    """MilestonePlannerAgent.run_with_milestone_list's real return type is
    List[MilestoneV2] (MA3.7) - this mock matches that real contract."""
    planner = MagicMock()
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
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


@pytest.mark.asyncio
async def test_plan_milestones_well_formed_v2_plan_passes_validation_first_try():
    """MA3.7: the planner now returns Schema v2 directly (either genuinely,
    or via that agent's own v1-fallback-then-normalize - see
    test_milestone_planner_agent_falls_back_to_v1_and_normalizes), and
    plan_milestones() validates it AS-IS, no normalization call on this live
    path any more (MilestonePlanValidator is the REAL, unmocked one here -
    unlike test_plan_milestones_retries_with_correction_feedback_on_rejection
    below, which mocks it out entirely to test the retry loop's wiring in
    isolation)."""
    planner = MagicMock()
    milestones = [
        mkv2("M1", goal="protocol", success_criterion="c1"),
        mkv2("M2", goal="cache", success_criterion="c2", depends_on=["M1"]),
    ]
    planner.run_with_milestone_list = AsyncMock(return_value=("raw", milestones))
    with tempfile.TemporaryDirectory() as tmp:
        state, err = await plan_milestones(planner, "goal", tmp)
    assert err is None
    assert planner.run_with_milestone_list.call_count == 1


@pytest.mark.asyncio
async def test_plan_milestones_retries_with_correction_feedback_on_rejection():
    """MA3.6: a rejected plan gets a reason-coded PLAN_VALIDATION_ERROR
    correction appended to the NEXT prompt, and a second, corrected attempt
    is accepted - proves the retry-loop wiring independent of whether
    today's v1 JSON contract can trigger a real rejection (see the test
    above)."""
    from unittest.mock import patch

    from kriya.workflow.milestone_validation import MilestoneValidationIssue, MilestoneValidationResult

    planner = MagicMock()
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
    prompts_seen = []

    async def fake_run(prompt, stream_callback=None):
        prompts_seen.append(prompt)
        return "raw", milestones

    planner.run_with_milestone_list = fake_run

    bad_result = MilestoneValidationResult(
        valid=False, milestones=[],
        errors=[MilestoneValidationIssue("UNJUSTIFIED_ENTRYPOINT", "M2", "test rejection reason")],
        warnings=[],
    )
    good_result = MilestoneValidationResult(valid=True, milestones=[], errors=[], warnings=[])

    with patch("kriya.workflow.milestones.MilestonePlanValidator") as mock_validator_cls:
        mock_validator_cls.return_value.validate = MagicMock(side_effect=[bad_result, good_result])
        with tempfile.TemporaryDirectory() as tmp:
            state, err = await plan_milestones(planner, "goal", tmp)

    assert err is None
    assert state is not None
    assert len(prompts_seen) == 2
    assert "PLAN_VALIDATION_ERROR" in prompts_seen[1]
    assert "UNJUSTIFIED_ENTRYPOINT" in prompts_seen[1]
    assert "test rejection reason" in prompts_seen[1]
    assert "PLAN_VALIDATION_ERROR" not in prompts_seen[0]


@pytest.mark.asyncio
async def test_plan_milestones_bounded_retry_never_infinite():
    """A plan rejected on EVERY attempt fails after exactly
    max_planning_attempts calls, never retries forever - the design doc's own
    explicit "no generic infinite re-planning loop" rule."""
    from unittest.mock import patch

    from kriya.workflow.milestone_validation import MilestoneValidationIssue, MilestoneValidationResult

    planner = MagicMock()
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
    call_count = {"n": 0}

    async def fake_run(prompt, stream_callback=None):
        call_count["n"] += 1
        return "raw", milestones

    planner.run_with_milestone_list = fake_run

    always_bad = MilestoneValidationResult(
        valid=False, milestones=[],
        errors=[MilestoneValidationIssue("EMPTY_ACCEPTANCE", "M1", "always rejected")],
        warnings=[],
    )
    with patch("kriya.workflow.milestones.MilestonePlanValidator") as mock_validator_cls:
        mock_validator_cls.return_value.validate = MagicMock(return_value=always_bad)
        with tempfile.TemporaryDirectory() as tmp:
            state, err = await plan_milestones(planner, "goal", tmp, max_planning_attempts=3)

    assert state is None
    assert err is not None
    assert "EMPTY_ACCEPTANCE" in err
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_plan_milestones_prompt_includes_real_repository_topology():
    """MA3.5: the planner prompt now carries deterministic, real repo
    evidence (not just the raw file listing) so the model doesn't have to
    guess whether it's planning against a single-module or multi-module
    project."""
    planner = MagicMock()
    captured = {}

    async def fake_run(prompt, stream_callback=None):
        captured["prompt"] = prompt
        return "raw", [mkv2("M1", goal="g1", success_criterion="c1")]

    planner.run_with_milestone_list = fake_run
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "pom.xml"), "w") as f:
            f.write("<project><groupId>x</groupId></project>")
        await plan_milestones(planner, "big goal", tmp)
    assert "Repository topology" in captured["prompt"]
    assert "Build system: maven" in captured["prompt"]


@pytest.mark.asyncio
async def test_plan_milestones_end_to_end_planner_correction_against_real_validator():
    """MA3.8 section 36's own required test: 'tests the actual safety
    architecture rather than only the validator function.' Unlike
    test_plan_milestones_retries_with_correction_feedback_on_rejection above
    (which mocks MilestonePlanValidator to isolate the retry-loop's own
    plumbing), this uses the REAL, unmocked validator against a REAL
    single-module Maven topology on disk: the planner's first response
    proposes 3 competing entrypoints (the historical incident's exact
    shape), gets genuinely rejected by MilestonePlanValidator, receives a
    real reason-coded correction, and its second, corrected response (single
    entrypoint, extended across all 3 milestones) is genuinely accepted."""
    planner = MagicMock()
    prompts_seen = []

    bad_plan = [
        MilestoneV2(id="M1", goal="read protocol messages", entrypoint="Protocol.java",
                    acceptance=[AcceptanceCriterion(id="M1-A1", description="message received")]),
        MilestoneV2(id="M2", goal="store the result", depends_on=["M1"], entrypoint="Cache.java",
                    acceptance=[AcceptanceCriterion(id="M2-A1", description="value stored")]),
        MilestoneV2(id="M3", goal="expose the result", depends_on=["M2"], entrypoint="Api.java",
                    acceptance=[AcceptanceCriterion(id="M3-A1", description="value readable")]),
    ]
    good_plan = [
        MilestoneV2(id="M1", goal="read protocol messages", entrypoint="Application.java",
                    acceptance=[AcceptanceCriterion(id="M1-A1", description="message received")]),
        MilestoneV2(id="M2", goal="store the result", depends_on=["M1"], mode=MilestoneMode.EXTENSION,
                    extends="M1", entrypoint="Application.java",
                    acceptance=[AcceptanceCriterion(id="M2-A1", description="value stored")]),
        MilestoneV2(id="M3", goal="expose the result", depends_on=["M2"], mode=MilestoneMode.EXTENSION,
                    extends="M2", entrypoint="Application.java",
                    acceptance=[AcceptanceCriterion(id="M3-A1", description="value readable")]),
    ]
    responses = [bad_plan, good_plan]

    async def fake_run(prompt, stream_callback=None):
        prompts_seen.append(prompt)
        return "raw", responses[len(prompts_seen) - 1]

    planner.run_with_milestone_list = fake_run
    goal = (
        "Create one Maven application that reads protocol messages, stores "
        "the resulting values, and exposes the result through the same "
        "application."
    )
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "pom.xml"), "w") as f:
            f.write("<project><groupId>x</groupId><artifactId>y</artifactId></project>")
        state, err = await plan_milestones(planner, goal, tmp)

    assert err is None, err
    assert state is not None
    assert len(prompts_seen) == 2
    assert "PLAN_VALIDATION_ERROR" in prompts_seen[1]
    assert "UNJUSTIFIED_ENTRYPOINT" in prompts_seen[1]
    assert "competing" in prompts_seen[1]
    assert [m.entrypoint for m in state.milestones] == ["Application.java"] * 3


# ============================================================
# render_repository_topology_summary
# ============================================================

def test_render_repository_topology_summary_empty_workspace():
    empty = RepositoryTopology(build_system=None, build_roots=(), modules=(), entrypoints=(), is_multi_module=False)
    text = render_repository_topology_summary(empty)
    assert "empty or new" in text


def test_render_repository_topology_summary_single_module():
    single = RepositoryTopology(
        build_system="maven", build_roots=(".",), modules=(),
        entrypoints=("com.example.Application",), is_multi_module=False,
    )
    text = render_repository_topology_summary(single)
    assert "Build system: maven" in text
    assert "Multi-module project: no" in text
    assert "com.example.Application" in text


def test_render_repository_topology_summary_multi_module():
    multi = RepositoryTopology(
        build_system="maven", build_roots=(".", "client", "server"), modules=("client", "server"),
        entrypoints=("A", "B"), is_multi_module=True,
    )
    text = render_repository_topology_summary(multi)
    assert "Multi-module project: yes" in text
    assert "client, server" in text


# ============================================================
# run_milestones control flow
# ============================================================

@pytest.mark.asyncio
async def test_run_milestones_success_path_through_all_milestones_and_integration():
    milestones = [
        mkv2("M1", goal="g1", success_criterion="c1"),
        mkv2("M2", goal="g2", success_criterion="c2", depends_on=["M1"]),
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
    assert state.completed_milestone_ids == ["M1", "M2"]


@pytest.mark.asyncio
async def test_run_milestones_registers_and_implements_declared_capabilities():
    """MA5.2/5.7's ContractRegistry bridge (kriya/control/contracts.py::
    contract_records_from_provided_capabilities/mark_capabilities_implemented)
    was genuinely dead code (zero callers anywhere) until wired into this
    exact real orchestration loop, 2026-08-24. A milestone that declares
    provides[] must end this run with a real, persisted, IMPLEMENTED
    ContractRecord - not just a planning-time validation signal that
    evaporates once the plan is accepted."""
    milestones = [
        mkv2("M1", goal="g1", success_criterion="c1", provides=[
            {"name": "ProtocolCodec", "description": "encode/decode Protocol objects"},
        ]),
        mkv2("M2", goal="g2", success_criterion="c2", depends_on=["M1"], provides=[{"name": "MainEntrypoint"}]),
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
        registry = load_contract_registry(tmp)
        codec = registry.get("M1:ProtocolCodec")
        assert codec.state == ContractState.IMPLEMENTED
        assert codec.shape == "encode/decode Protocol objects"
        assert registry.get("M2:MainEntrypoint").state == ContractState.IMPLEMENTED


@pytest.mark.asyncio
async def test_run_milestones_populates_real_contract_consumers_from_consumes():
    """2026-08-25 external review, P0 finding: contract_records_from_
    provided_capabilities() registered the provider side of every
    declared capability but never populated ContractRecord.consumers from
    the matching milestone.consumes[] - so ContractChange.affected_consumers
    could never actually name a real downstream milestone. Wired via
    wire_contract_consumers(), called once per run_milestones() invocation
    right after every milestone's provides[] is registered."""
    milestones = [
        mkv2("M1", goal="g1", success_criterion="c1", provides=[{"name": "ProtocolCodec"}]),
        mkv2("M2", goal="g2", success_criterion="c2", depends_on=["M1"], consumes=["ProtocolCodec"]),
        mkv2("M4", goal="g4", success_criterion="c4", depends_on=["M1"], consumes=["ProtocolCodec"]),
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
        registry = load_contract_registry(tmp)
        assert registry.get("M1:ProtocolCodec").consumers == ("M2", "M4")


@pytest.mark.asyncio
async def test_run_milestones_invalidates_a_completed_consumer_when_its_providers_contract_changes():
    """MA7-C3 (2026-08-25 external review): the real end-to-end scenario
    this architecture can actually produce - a hand-edited/re-planned
    milestone file changes M1's declared capability shape between two
    separate run_milestones() invocations for the SAME workspace, while M2
    (a real consumer, wired via MA7-C2) already completed in an EARLIER
    partial run against the now-stale shape. M2 must be treated as
    invalidated - removed from completed_milestone_ids and genuinely
    re-executed - not silently skipped as if nothing changed. M1 itself
    (the provider) is NOT re-executed by this mechanism - only its
    consumer is, matching the review's own one-level example exactly."""
    with tempfile.TemporaryDirectory() as tmp:
        # Simulate a PRIOR, completed run: M1's contract registered+
        # IMPLEMENTED with its OLD shape, M2 wired as a real consumer.
        from kriya.control.contracts import ContractRegistry
        from kriya.control.persistence import save_contract_registry

        prior_registry = ContractRegistry()
        prior_registry.register(
            contract_id="M1:ProtocolCodec", name="ProtocolCodec", provider_milestone_id="M1",
            shape="OLD shape: encode/decode with 4 fields", consumers=("M2",),
        )
        prior_registry.approve("M1:ProtocolCodec")
        prior_registry.freeze("M1:ProtocolCodec")
        prior_registry.mark_implemented("M1:ProtocolCodec")
        save_contract_registry(tmp, prior_registry)

        # A re-plan (hand-edited or freshly re-planned) changes M1's
        # declared shape - M2 stays otherwise identical.
        milestones = [
            mkv2("M1", goal="g1", success_criterion="c1", provides=[
                {"name": "ProtocolCodec", "description": "NEW shape: encode/decode with 6 fields"},
            ]),
            mkv2("M2", goal="g2", success_criterion="c2", depends_on=["M1"], consumes=["ProtocolCodec"]),
            mkv2("M3", goal="g3", success_criterion="c3", depends_on=["M2"]),
        ]
        state = MilestoneRunState(
            group_id="grp", original_goal="orig", milestones=milestones,
            completed_milestone_ids=["M1", "M2", "M3"],
        )
        save_checkpoint(tmp, "stale-m3", {
            "stage": "developer",
            "milestone_group_id": "grp",
            "milestone_index": 3,
        })
        save_control_state(tmp, ControlState(
            schema_version=CURRENT_SCHEMA_VERSION,
            run_id="prior-run",
            milestone_group_id="grp",
            milestone_states={"M1": "done", "M2": "done", "M3": "done"},
            current_plan_hash="old-plan",
            last_verified_checkpoint="stale-m3",
        ))
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(
            return_value={"quality_gates_passed": True, "design": "d", "files": ["b.py"]}
        )
        we.run_verifier = MagicMock()
        we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})

        result = await run_milestones(we, state, tmp)

        assert result["status"] == "success"
        # M1 stays skipped (the provider itself isn't re-executed by this
        # mechanism) - M2 and its transitive dependent M3 get real calls,
        # plus the final integration pass every successful run makes
        # (see test_run_milestones_success_path_through_all_milestones_and_
        # integration's own "2 milestones + integration" precedent) - three
        # calls total, since M1 stays correctly skipped.
        assert we.run_generation_workflow.await_count == 3
        assert "M1" in state.completed_milestone_ids
        assert "M2" in state.completed_milestone_ids  # re-added once it completes again, for real
        assert "M3" in state.completed_milestone_ids
        assert state.stale_milestone_ids == []
        assert not os.path.exists(checkpoint_path(tmp, "stale-m3"))
        evidence = state.invalidation_evidence[-1]
        assert evidence["transitively_invalidated"] == ["M2", "M3"]
        assert evidence["invalidated_checkpoints"] == ["stale-m3"]
        assert evidence["replacement_plan_revalidated"] is True
        assert len(evidence["replacement_plan_hash"]) == 64
        control_state = load_control_state(tmp)
        assert control_state.milestone_states["M2"] == "done"
        assert control_state.milestone_states["M3"] == "done"
        assert control_state.current_plan_hash == evidence["replacement_plan_hash"]
        assert control_state.last_verified_checkpoint is None
        registry = load_contract_registry(tmp)
        assert registry.get("M1:ProtocolCodec").shape == "NEW shape: encode/decode with 6 fields"


@pytest.mark.asyncio
async def test_run_milestones_capability_stays_proposed_when_its_milestone_fails():
    """The providing milestone never actually completing must leave its
    declared capability at PROPOSED, not silently advanced - an honest
    signal that the capability never became real, distinct from a
    milestone that simply never declared one at all."""
    milestones = [mkv2("M1", goal="g1", success_criterion="c1", provides=[{"name": "ProtocolCodec"}])]
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=milestones)
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(
            return_value={"quality_gates_passed": False, "design": "d", "files": []}
        )

        result = await run_milestones(we, state, tmp)

        assert result["status"] == "milestone_failed"
        registry = load_contract_registry(tmp)
        assert registry.get("M1:ProtocolCodec").state == ContractState.PROPOSED


@pytest.mark.asyncio
async def test_run_milestones_derives_and_persists_real_artifacts_per_milestone():
    """External review P1, 'ArtifactRegistry-for-milestones consolidation'
    (2026-08-25): WorkflowController's own enforce-mode subtask loop already
    had real ArtifactRegistry derivation (MA7.10) - run_milestones() had
    zero equivalent wiring (confirmed via grep before this change, ZERO
    references to ArtifactRegistry anywhere in this module). A milestone
    that completes for real, in a workspace with a real ecosystem marker,
    must end this run with a real, persisted ArtifactRecord keyed by that
    milestone's own real id - deliberately per-milestone as each one
    completes (not batched to the end like enforce mode does for its own,
    different reason), matching this function's existing completed_
    milestone_ids/established_file_context incremental-persistence
    convention."""
    milestones = [
        mkv2("M1", goal="g1", success_criterion="c1"),
        mkv2("M2", goal="g2", success_criterion="c2", depends_on=["M1"]),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "pyproject.toml"), "w") as f:
            f.write('[project]\nname = "myapp"\nversion = "0.1.0"\n')

        state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=milestones)
        we = MagicMock()
        we.run_generation_workflow = AsyncMock(
            return_value={"quality_gates_passed": True, "design": "d", "files": ["a.py"]}
        )
        we.run_verifier = MagicMock()
        we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})

        result = await run_milestones(we, state, tmp)

        assert result["status"] == "success"
        from kriya.control.persistence import load_artifact_registry
        registry = load_artifact_registry(tmp)
        m1_records = registry.resolve_for_milestone("M1")
        assert len(m1_records) == 1
        assert m1_records[0].ecosystem == "python"
        assert m1_records[0].coordinates == {"name": "myapp", "version": "0.1.0"}
        # Both milestones share the same real workspace-level pyproject.toml, so
        # each one's own real derivation pass finds and records it too, keyed
        # under its OWN milestone id - real per-milestone attribution, not a
        # single global record.
        assert len(registry.resolve_for_milestone("M2")) == 1


@pytest.mark.asyncio
async def test_run_milestones_artifact_derivation_is_non_fatal_and_skips_an_unrecognized_ecosystem():
    """No pom.xml/pyproject.toml/package.json in the workspace at all -
    ArtifactRegistry.derive_from_workspace() correctly yields nothing
    (honest absence, per its own docstring), and the run must still succeed
    exactly as before this wiring existed."""
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
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
        from kriya.control.persistence import load_artifact_registry
        registry = load_artifact_registry(tmp)
        assert registry.resolve_for_milestone("M1") == ()


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
        mkv2("M1", goal="Build Protocol class", success_criterion="c1"),
        mkv2("M2", goal="Write a main class using Protocol", success_criterion="c2", depends_on=["M1"]),
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
    assert first_call_kwargs["established_files"] == []

    # Milestone 2's call must carry milestone 1's real file content forward,
    # AND the filepath itself via established_files - the latter is what lets
    # a correct self-diagnosis naming "the Protocol class" actually redirect a
    # targeted retry to src/Protocol.java (found live, 2026-08-21,
    # ignite_qpid_protocol milestone 2/4: supplementary_context alone grounds
    # what the model ASSUMES about an earlier file, but does nothing for a
    # LATER failure whose fix requires editing that file, since only files
    # THIS milestone's own attempts wrote were ever valid redirect targets).
    second_call_kwargs = we.run_generation_workflow.await_args_list[1].kwargs
    assert real_signature in second_call_kwargs["supplementary_context"]
    assert "src/Protocol.java" in second_call_kwargs["supplementary_context"]
    assert second_call_kwargs["established_files"] == ["src/Protocol.java"]

    # The integration pass gets both too.
    integration_call_kwargs = we.run_generation_workflow.await_args_list[2].kwargs
    assert real_signature in integration_call_kwargs["supplementary_context"]
    assert integration_call_kwargs["established_files"] == ["src/Protocol.java"]


@pytest.mark.asyncio
async def test_run_milestones_passes_pom_content_to_judge_for_exec_shape_inference():
    """Regression guard for a real finding: RunVerifierAgent.judge() reliably
    mis-infers exec:java for a pom actually shaped for exec:exec when called
    without build_file_content (confirmed, documented in judge()'s own
    prompt/docstring) - exactly the goal shape (Ignite/Qpid, needing
    --add-opens) this whole feature was built around. Must read and pass the
    real, current pom.xml, same convention as attempt.py's own call site."""
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
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
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
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
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
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
        mkv2("M1", goal="g1", success_criterion="c1"),
        mkv2("M2", goal="g2", success_criterion="c2", depends_on=["M1"]),
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
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
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
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
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
    assert state.completed_milestone_ids == []


@pytest.mark.asyncio
async def test_run_milestones_dependency_regression_retry_recovers():
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
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
    assert state.completed_milestone_ids == ["M1"]


@pytest.mark.asyncio
async def test_run_milestones_resume_skips_already_completed():
    milestones = [
        mkv2("M1", goal="g1", success_criterion="c1"),
        mkv2("M2", goal="g2", success_criterion="c2", depends_on=["M1"]),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(
            group_id="grp", original_goal="orig", milestones=milestones,
            completed_milestone_ids=["M1"],
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
            milestones=[mkv2("M1", goal="g1", success_criterion="c1")],
            verification_commands={"M1": [["echo", "hi"]]},
        )
        mock_validator = MagicMock()
        mock_validator.run_app_sequence.return_value = {
            "timed_out": False, "output": "[VERIFICATION] FAIL: something broke",
        }
        with patch("kriya.tools.validate.PolymorphicValidator", return_value=mock_validator), \
             patch("kriya.workflow.milestones._resolve_run_command", side_effect=lambda c, w: c):
            failures = replay_prior_milestone_verifications(tmp, state)
    assert len(failures) == 1
    assert failures[0]["milestone_id"] == "M1"
    assert "something broke" in failures[0]["reason"]


def test_replay_no_marker_is_not_treated_as_failure():
    with tempfile.TemporaryDirectory() as tmp:
        state = MilestoneRunState(
            group_id="grp", original_goal="orig",
            milestones=[mkv2("M1", goal="g1", success_criterion="c1")],
            verification_commands={"M1": [["echo", "hi"]]},
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
            milestones=[mkv2("M1", goal="g1", success_criterion="c1")],
            verification_commands={"M1": [["sleep", "999"]]},
        )
        mock_validator = MagicMock()
        mock_validator.run_app_sequence.return_value = {"timed_out": True, "output": ""}
        with patch("kriya.tools.validate.PolymorphicValidator", return_value=mock_validator), \
             patch("kriya.workflow.milestones._resolve_run_command", side_effect=lambda c, w: c):
            failures = replay_prior_milestone_verifications(tmp, state)
    assert len(failures) == 1
    assert failures[0]["milestone_id"] == "M1"
    assert "timed out" in failures[0]["reason"]


@pytest.mark.asyncio
async def test_run_milestones_stops_before_integration_call_on_replay_failure():
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
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
            return_value=[{"milestone_id": "M1", "reason": "regressed"}],
        ):
            result = await run_milestones(we, state, tmp)

    assert result["status"] == "milestone_replay_failed"
    assert result["failures"][0]["milestone_id"] == "M1"
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


# ============================================================
# MA3.9 - milestone-plan telemetry
# ============================================================

def test_trace_logger_persists_milestone_plan_row():
    from kriya.core.trace import TraceLogger
    import sqlite3
    import json as _json

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "traces.db")
        t = TraceLogger(db_path)
        t.log_milestone_plan(
            group_id="grp1", status="accepted", schema_version=2, milestone_count=3,
            dependency_edges=2, extension_count=2, composition_count=0,
            validation_attempts=1, validation_failures=[],
            repository_topology={"build_system": "maven", "module_count": 1, "entrypoint_count": 1},
        )
        t.close()

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, schema_version, milestone_count, dependency_edges, "
            "extension_count, composition_count, validation_attempts, "
            "validation_failures, repository_topology FROM milestone_plans WHERE group_id = ?",
            ("grp1",),
        ).fetchone()
        conn.close()

    assert row[:7] == ("accepted", 2, 3, 2, 2, 0, 1)
    assert _json.loads(row[7]) == []
    assert _json.loads(row[8]) == {"build_system": "maven", "module_count": 1, "entrypoint_count": 1}


def test_plan_structure_telemetry_counts_edges_and_modes():
    from kriya.workflow.milestone_validation import plan_structure_telemetry

    m1 = mkv2("M1", goal="g1", success_criterion="c1")
    m2 = mkv2("M2", goal="g2", success_criterion="c2", depends_on=["M1"], mode=MilestoneMode.EXTENSION, extends="M1")
    m3 = mkv2("M3", goal="g3", success_criterion="c3", depends_on=["M1", "M2"], mode=MilestoneMode.COMPOSITION)
    assert plan_structure_telemetry([m1, m2, m3]) == {
        "milestone_count": 3, "dependency_edges": 3, "extension_count": 1, "composition_count": 1,
    }


@pytest.mark.asyncio
async def test_plan_milestones_logs_telemetry_when_accepted():
    import sqlite3
    import json as _json

    planner = MagicMock()
    milestones = [mkv2("M1", goal="g1", success_criterion="c1"), mkv2("M2", goal="g2", success_criterion="c2", depends_on=["M1"])]
    planner.run_with_milestone_list = AsyncMock(return_value=("raw", milestones))
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as logs:
        state, err = await plan_milestones(planner, "goal", tmp, logs_path=logs)
        assert err is None
        conn = sqlite3.connect(os.path.join(logs, "traces.db"))
        row = conn.execute(
            "SELECT status, milestone_count, validation_attempts, validation_failures "
            "FROM milestone_plans WHERE group_id = ?", (state.group_id,),
        ).fetchone()
        conn.close()
    assert row[0] == "accepted"
    assert row[1] == 2
    assert row[2] == 1
    assert _json.loads(row[3]) == []


@pytest.mark.asyncio
async def test_plan_milestones_without_logs_path_does_no_io():
    """logs_path defaults to None - a caller that doesn't pass it (most of
    this module's own tests, any pre-MA3.9 caller) gets zero telemetry I/O,
    not an error."""
    planner = MagicMock()
    milestones = [mkv2("M1", goal="g1", success_criterion="c1")]
    planner.run_with_milestone_list = AsyncMock(return_value=("raw", milestones))
    with tempfile.TemporaryDirectory() as tmp:
        state, err = await plan_milestones(planner, "goal", tmp)
    assert err is None
