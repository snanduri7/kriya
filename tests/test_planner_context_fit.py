"""PLANNER-CONTEXT-FIT-001: the enforce Planner request is fitted by priority.

Measured (2026-10-03, commons-lang `chop`, Planner qwen3.6): the production
request was 49,763 chars, about 32k of them 239 ``source references ->
target`` structural lines. Only the fenced reference was fitted, so with
qwen3.6's qualified admission ratio (1.6852 bytes/token) the dispatch check
estimated 33,599 prompt tokens against a 32,768 window and refused it before
inference (CONTEXT_BUDGET_UNSATISFIABLE); the same-size request to qwen3-coder
reported 12,273 real input tokens. Admission is unchanged here: the request is
made to fit it. The structural relationships give way by priority - lines
touching no grounded owner or Code Intelligence candidate first, then those of
lower-ranked candidates - whole lines only, with an explicit elision line; the
fenced reference gives way before them; goal, protocol, requirements, owners
and the candidate map (annotations, configuration values) are never trimmed.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from kriya.config.config import FallbackModelConfig
from kriya.workflow import context_budget as budget
from kriya.workflow.context_budget import (
    STRUCTURAL_EVIDENCE_ELISION,
    RequestCapacity,
    fit_structural_evidence,
)
from kriya.workflow.graph_retrieval import LocalizationCandidate
from kriya.workflow.ownership_findings import OwnerCandidate
from kriya.workflow.untrusted_context import UNTRUSTED_REFERENCE_BEGIN

XML = "src/main/resources/spring/tools-config.xml"
OWNER = "src/main/java/shop/ItemCatalog.java"
SERVICE = "src/main/java/shop/ItemServiceImpl.java"
RATIO = 1.6852  # qwen3.6's qualified bytes/token floor (the measured refusal)


def _edge(source, target):
    return f"{source} references -> {target}"


def _lines(unrelated=200):
    """Focused relationships interleaved with unrelated ones, in the
    builder's sorted order."""
    lines = [_edge(f"src/main/java/shop/Caller{i}.java", SERVICE) for i in range(6)]
    lines += [_edge(SERVICE, "src/main/java/shop/ItemRepository.java"), _edge(XML, SERVICE),
              _edge(OWNER, "src/main/java/shop/ItemKind.java")]
    lines += [_edge(f"src/main/java/other/pkg{i}/Thing{i}.java", f"src/main/java/other/pkg{i}/Helper{i}.java")
              for i in range(unrelated)]
    return sorted(lines)


FOCUS = {SERVICE: 0, XML: 1}


def _capacity_for(text_tokens):
    return RequestCapacity(tokens=text_tokens, bytes_per_token=RATIO, non_ascii_bytes_per_token=RATIO)


def test_a_section_that_fits_is_unchanged():
    lines = _lines(10)
    fit, accounting = fit_structural_evidence(_capacity_for(100_000), ("fixed",), lines, FOCUS)
    assert fit.value == "\n".join(lines)
    assert accounting["lines_kept"] == accounting["lines_total"] == len(lines) and accounting["lines_omitted"] == 0


def test_unrelated_relationships_give_way_first_and_every_kept_line_is_whole():
    lines = _lines()
    focused = [line for line in lines if SERVICE in line or XML in line]  # OWNER is not in FOCUS here
    capacity = _capacity_for(RequestCapacity(tokens=0, bytes_per_token=RATIO).count("\n".join(focused)) + 120)
    fit, accounting = fit_structural_evidence(capacity, (), lines, FOCUS)
    shown = fit.value.split("\n")
    assert shown[-1] == STRUCTURAL_EVIDENCE_ELISION.format(omitted=accounting["lines_omitted"], total=len(lines))
    assert set(focused) <= set(shown[:-1])  # every relationship of a candidate file is kept
    assert all(line in lines for line in shown[:-1])  # whole lines, never cut
    assert shown[:-1] == [line for line in lines if line in set(shown[:-1])]  # original order
    assert 0 < accounting["lines_omitted"] < len(lines) and accounting["focus_lines"] == len(focused)
    assert capacity.count(fit.value) <= capacity.tokens


def test_lower_ranked_candidates_give_way_before_higher_ranked_ones():
    lines = sorted([_edge(f"a/Caller{i}.java", SERVICE) for i in range(40)]
                   + [_edge(f"b/User{i}.java", XML) for i in range(40)])
    service_lines = [line for line in lines if SERVICE in line]
    capacity = _capacity_for(RequestCapacity(tokens=0, bytes_per_token=RATIO).count("\n".join(service_lines)) + 200)
    fit, _ = fit_structural_evidence(capacity, (), lines, FOCUS)
    shown = set(fit.value.split("\n"))
    assert set(service_lines) <= shown  # rank 0 kept whole
    assert any(XML in line for line in lines if line not in shown)  # rank 1 trimmed


def test_no_room_leaves_only_the_elision_line():
    lines = _lines(5)
    fit, accounting = fit_structural_evidence(_capacity_for(10), ("x" * 1000,), lines, FOCUS)
    assert fit.omitted and fit.value == STRUCTURAL_EVIDENCE_ELISION.format(omitted=len(lines), total=len(lines))
    assert accounting["lines_kept"] == 0


# --- through the real enforce Planner request builder ---------------------------------------

CANDIDATES = [
    LocalizationCandidate("java:svc#findItems", SERVICE, "method", "shop.ItemServiceImpl.findItems",
                          "@Cacheable public Collection<Item> findItems()", 1.0, (("fts", 1.0),)),
    LocalizationCandidate("xml:tools#cacheNames", XML, "bean_property", "cacheManager.cacheNames",
                          '<property name="cacheNames"><set><value>items</value></set></property>', 0.9,
                          (("fts", 1.0),)),
]


def _enforce_prompts(tmp_path, lines, reference=""):
    from test_auth_goal_contamination_001 import _plan_copying
    from test_workflow_controller import _workflow_engine

    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.workflow_controller import WorkflowController

    engine = _workflow_engine()
    engine.planner.role_llm = None
    engine.planner.run = AsyncMock(return_value="structured plan")
    plan = _plan_copying("ok")

    async def generation(**kwargs):
        with open(f"{kwargs['workspace_path']}/a.py", "w", encoding="utf-8") as handle:
            handle.write("# generated\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["a.py"]}

    engine.run_generation_workflow = AsyncMock(side_effect=generation)
    workspace = tmp_path / "enforce"
    workspace.mkdir()
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(
             valid=True))), \
         patch("kriya.workflow.workflow_controller.build_planning_structural_evidence",
               return_value=("\n".join(lines), {})), \
         patch("kriya.workflow.workflow_controller.localization_candidates", new=AsyncMock(return_value=CANDIDATES)), \
         patch("kriya.workflow.workflow_controller.grounded_owner_candidates", return_value=[OwnerCandidate(
             path=OWNER, role="item", shared=("item",), members=(), referenced_by=())]):
        asyncio.run(WorkflowController(engine).execute(
            "Cache the item kinds the same way the items are cached.", str(workspace), migration_mode="enforce",
            reference_context=reference))
    call = engine.planner.run.await_args_list[0]
    return call.args[0], call.kwargs["candidate_prompt"]


def _sized(prompts, tokens):
    """The prompt built for a candidate whose request has ``tokens`` room at
    the measured qwen3.6 ratio."""
    candidate = FallbackModelConfig(model="planner-under-test", context_window=32768)
    with patch.object(budget, "candidate_request_capacity", return_value=_capacity_for(tokens)):
        return prompts(candidate)


def _count(text):
    return RequestCapacity(tokens=0, bytes_per_token=RATIO, non_ascii_bytes_per_token=RATIO).count(text)


def test_a_request_that_fits_goes_out_byte_identical(tmp_path):
    from kriya.workflow.workflow_controller import AUTHORITATIVE_PLANNER_SYSTEM_PROMPT

    lines = _lines(10)
    first, prompts = _enforce_prompts(tmp_path, lines)
    assert "\n".join(lines) in first
    roomy = _sized(prompts, _count(AUTHORITATIVE_PLANNER_SYSTEM_PROMPT + first) + 50)
    assert roomy == first


def test_an_oversized_request_is_admitted_with_candidate_facts_and_protocol_intact(tmp_path):
    """The refused shape: mandatory text fits, the relationships do not."""
    from kriya.workflow.workflow_controller import AUTHORITATIVE_PLANNER_SYSTEM_PROMPT

    lines = _lines(400)
    first, prompts = _enforce_prompts(tmp_path, lines)
    full = _count(AUTHORITATIVE_PLANNER_SYSTEM_PROMPT + first)
    edges = _count("\n".join(lines))
    room = full - edges // 2  # half the relationships cannot fit
    fitted = _sized(prompts, room)
    assert _count(AUTHORITATIVE_PLANNER_SYSTEM_PROMPT + fitted) <= room < full
    # P0/P1/P3 untouched: everything outside the relationships block is byte-identical
    before, after = first.split("\n".join(lines))
    assert fitted.startswith(before) and fitted.endswith(after)
    for fact in ("@Cacheable public Collection<Item> findItems()",
                 '<property name="cacheNames"><set><value>items</value></set></property>',
                 "Original product request:", "=== Original Requirements", "Every verification entry must be"):
        assert fact in fitted
    shown = fitted[len(before):len(fitted) - len(after)].split("\n")
    assert all(line in shown for line in lines if SERVICE in line or XML in line or OWNER in line)
    assert shown[-1].startswith("(") and "structural relationships not shown" in shown[-1]


def test_the_untrusted_reference_gives_way_before_relationships(tmp_path):
    from kriya.workflow.workflow_controller import AUTHORITATIVE_PLANNER_SYSTEM_PROMPT

    lines = _lines(10)
    reference = "".join(f"\n[Source: doc{i}]\n" + "background text " * 60 for i in range(20))
    first, prompts = _enforce_prompts(tmp_path, lines, reference=reference)
    assert UNTRUSTED_REFERENCE_BEGIN in first
    without_reference = first[:first.index("\n" + UNTRUSTED_REFERENCE_BEGIN)] if (
        "\n" + UNTRUSTED_REFERENCE_BEGIN) in first else first[:first.index(UNTRUSTED_REFERENCE_BEGIN)]
    fitted = _sized(prompts, _count(AUTHORITATIVE_PLANNER_SYSTEM_PROMPT + without_reference) + 40)
    assert "\n".join(lines) in fitted  # every relationship kept
    assert fitted.count("[Source: ") < first.count("[Source: ")


def test_a_request_whose_mandatory_text_cannot_fit_still_fails_closed(tmp_path):
    from kriya.workflow.workflow_controller import AUTHORITATIVE_PLANNER_SYSTEM_PROMPT

    lines = _lines(50)
    first, prompts = _enforce_prompts(tmp_path, lines)
    fitted = _sized(prompts, 200)
    assert STRUCTURAL_EVIDENCE_ELISION.format(omitted=len(lines), total=len(lines)) in fitted
    assert _count(AUTHORITATIVE_PLANNER_SYSTEM_PROMPT + fitted) > 200  # the dispatch check refuses it, unchanged



def test_a_higher_priority_line_is_never_displaced_by_a_lower_one():
    """Kept lines are a priority prefix: a focused line too long for the room
    left is not replaced by shorter unrelated lines after it."""
    long_focused = _edge("src/main/java/shop/" + "Very" * 60 + "LongCaller.java", SERVICE)
    lines = [_edge(f"x/A{i}.java", SERVICE) for i in range(5)] + [long_focused] + \
        [_edge(f"y/B{i}.java", f"y/C{i}.java") for i in range(30)]
    head = "\n".join(lines[:5])
    capacity = _capacity_for(RequestCapacity(tokens=0, bytes_per_token=RATIO).count(head) + 150)
    fit, _ = fit_structural_evidence(capacity, (), lines, FOCUS)
    shown = fit.value.split("\n")[:-1]
    assert long_focused not in shown and not any(line.startswith("y/") for line in shown)
