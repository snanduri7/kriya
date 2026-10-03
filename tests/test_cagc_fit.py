"""CAGC-0 canonical-fit integration (KRIYA_CAGC v0.7 §7, §8.3, §9, §14
test_cagc_fit): guidance is fitted only by the existing fitters
(fit_variable_section / fit_developer_request), always rebuilt from its
original block, placed after the skills text, never selected by evidence the
fit removed, and dropped before any file a review carries."""
import ast
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kriya.capabilities.guidance import (
    CapabilityGuidance,
    Domain,
    GuidanceRule,
    Operation,
    RepositoryFacts,
    Role,
)
from kriya.capabilities.guidance.facts import BuildRootFact
from kriya.workflow import capability_guidance as cg
from kriya.workflow import context_budget as budget
from kriya.workflow.context_budget import (
    DEVELOPER_SECTION_ORDER,
    OptionalSection,
    RequestCapacity,
    estimate_tokens,
    fit_developer_request,
    fit_planner_request,
)


def _rule(rule_id, priority, role=Role.DEVELOPER, words=8):
    return GuidanceRule(rule_id, " ".join(["word"] * words) + ".", frozenset({role}), frozenset(), frozenset(),
                        priority, "test")


REGISTRY = (
    CapabilityGuidance("java", 1, Domain.LANGUAGE, tuple(
        _rule(f"java.dev.r{i}", i) for i in range(3)) + tuple(_rule(f"java.plan.r{i}", i, Role.PLANNER)
                                                              for i in range(3))),
    CapabilityGuidance("maven", 1, Domain.BUILD, (_rule("maven.plan.m", 1, Role.PLANNER),)),
    CapabilityGuidance("python", 1, Domain.LANGUAGE, (_rule("python.plan.p", 1, Role.PLANNER),)),
    *(CapabilityGuidance(name, 1, Domain.FRAMEWORK, ()) for name in ("spring", "spring_xml", "spring_config")),
    *(CapabilityGuidance(name, 1, Domain.BUILD, ()) for name in ("gradle", "pip")),
)
REPO = RepositoryFacts(False, False, (BuildRootFact("", frozenset({"maven"})),))


def _compose(facts):
    from kriya.capabilities.guidance import compose_guidance

    return compose_guidance(facts, registry=REGISTRY)


@pytest.fixture(autouse=True)
def _small_registry():
    """The ablation seam: compose_guidance(..., registry=...)."""
    with patch("kriya.workflow.capability_guidance.compose_guidance", _compose):
        yield


def _section(role, paths, sink=None, operation=None):
    operation = operation or (Operation.PLAN if role in (Role.PLANNER, Role.ARCHITECT) else Operation.EDIT)
    return cg.compose_section(role, operation, REPO, paths, lambda _p: None, sink)


# -- the Developer -------------------------------------------------------------


def test_developer_order_puts_guidance_after_siblings_and_before_graph():
    assert DEVELOPER_SECTION_ORDER == ("planned_source", "siblings", "capability_guidance", "graph_context",
                                       "investigation", "learned_reference")


def _developer_prompt(guidance_text, graph_text, planned_text):
    return "SKILLS\n" + guidance_text + planned_text + graph_text + "\nTASK"


def test_developer_fit_trims_graph_before_guidance_and_guidance_before_planned_source():
    sent = []
    section = _section(Role.DEVELOPER, ["A.java"], lambda facts, block: sent.append(block))
    assert section.text and section.base.rule_ids == ("java.dev.r0", "java.dev.r1", "java.dev.r2")
    planned = "\nPLANNED " + "p" * 400
    graph = "\nGRAPH " + "g" * 4000
    prompt = _developer_prompt(section.text, graph, planned)
    sections = (OptionalSection("planned_source", planned, lambda b: ""),
                OptionalSection("capability_guidance", section.text, section.rebuild, section.observe),
                OptionalSection("graph_context", graph, lambda b: ""))
    capacity = RequestCapacity(tokens=budget.RequestCapacity(tokens=0).count(prompt) - 500)
    fitted, details = fit_developer_request(capacity, "", prompt, sections)
    assert planned in fitted and section.text in fitted and graph not in fitted
    assert details["sections"]["graph_context"]["reduced"]
    assert sent == [section.base], "the event describes the block actually sent"
    # Less room: guidance gives way (by whole rules) before the planned source does.
    sent.clear()
    tight = RequestCapacity(tokens=capacity.count(prompt) - capacity.count(graph) - 30)
    fitted, _ = fit_developer_request(tight, "", prompt, sections)
    assert planned in fitted
    assert len(sent) == 1 and len(sent[0].rule_ids) < len(section.base.rule_ids)
    assert sent[0].text in fitted and sent[0].dropped_by_fit_rule_ids


def test_developer_request_that_fits_is_byte_identical_and_still_reported():
    sent = []
    section = _section(Role.DEVELOPER, ["A.java"], lambda facts, block: sent.append(block))
    prompt = _developer_prompt(section.text, "\nGRAPH", "\nPLANNED")
    sections = (OptionalSection("capability_guidance", section.text, section.rebuild, section.observe),)
    fitted, details = fit_developer_request(RequestCapacity(tokens=100_000), "", prompt, sections)
    assert fitted == prompt and details == {}
    assert sent == [section.base]


def test_empty_guidance_is_reported_with_zero_tokens():
    sent = []
    section = _section(Role.DEVELOPER, ["a.py"], lambda facts, block: sent.append((facts, block)))
    assert section.text == ""
    sections = (OptionalSection("capability_guidance", section.text, section.rebuild, section.observe),)
    fit_developer_request(RequestCapacity(tokens=100_000), "", "PROMPT", sections)
    (facts, block), = sent
    assert block.estimated_tokens == 0 and block.rule_ids == () and facts.role is Role.DEVELOPER


def test_guidance_absent_from_the_request_is_reported_as_not_sent():
    sent = []
    section = _section(Role.DEVELOPER, ["A.java"], lambda facts, block: sent.append(block))
    sections = (OptionalSection("capability_guidance", section.text, section.rebuild, section.observe),)
    fit_developer_request(RequestCapacity(tokens=100_000), "", "A PROMPT WITHOUT IT", sections)
    assert sent[0].text == "" and set(sent[0].dropped_by_fit_rule_ids) == set(section.base.rule_ids)


def test_rebuild_always_refits_the_original_block():
    section = _section(Role.DEVELOPER, ["A.java"])
    full = section.base.estimated_tokens
    small = section.rebuild(full - 10)
    smaller = section.rebuild(estimate_tokens(small) - 10)
    again = section.rebuild(full)
    assert len(smaller) < len(small) < len(again) and again == section.text
    assert section.sent(again) == section.base
    with pytest.raises(ValueError):
        section.sent("text this section never produced")


# -- the direct Planner and the Architect --------------------------------------


def _package(paths):
    return SimpleNamespace(relevant_files=tuple(SimpleNamespace(path=p) for p in paths), omitted=())


def _planner(capacity, guidance, graph_context, rebuild_graph, reference=""):
    return fit_planner_request(
        capacity, system_prompt="SYS", head="HEAD\n", skills_prompt="SKILLS\n", graph_context=graph_context,
        reference=reference, suffix="\nSUFFIX", rebuild_graph=rebuild_graph, guidance=guidance,
        graph_package=_package(["A.java", "app.py"]))


def test_planner_guidance_sits_between_skills_and_graph_and_fits_byte_identically():
    sections = []

    def guidance(shown):
        sections.append(_section(Role.PLANNER, [p for p in ("A.java", "app.py") if p in shown]))
        return sections[-1]

    prompt, details = _planner(RequestCapacity(tokens=100_000), guidance, "GRAPH\n", lambda b: ("", None))
    text = sections[0].text
    assert text and prompt == "HEAD\nSKILLS\n" + text + "GRAPH\n" + "\nSUFFIX"
    assert details == {}
    assert prompt.index("SKILLS") < prompt.index(text) < prompt.index("GRAPH")


def test_an_omitted_graph_file_does_not_select_its_capability():
    shown_calls = []

    def guidance(shown):
        shown_calls.append(tuple(shown))
        return _section(Role.PLANNER, [p for p in ("A.java", "app.py") if p in shown])

    big_graph = "G" * 8000

    def rebuild(budget_units):
        return "SMALL GRAPH\n", _package(["A.java"])  # app.py no longer shown

    capacity = RequestCapacity(tokens=RequestCapacity(tokens=0).count(big_graph) // 2)
    prompt, details = _planner(capacity, guidance, big_graph, rebuild)
    assert shown_calls == [("A.java",)]
    assert "[python]" not in prompt and "[java]" in prompt
    assert details["graph"]["builds"] >= 1


def test_graph_left_out_entirely_selects_nothing_from_it():
    shown_calls = []

    def guidance(shown):
        shown_calls.append(tuple(shown))
        return _section(Role.PLANNER, list(shown))

    prompt, _ = _planner(RequestCapacity(tokens=40), guidance, "G" * 8000, lambda b: ("", _package([])))
    assert shown_calls == [()]


def test_planner_guidance_gives_way_before_graph_and_reference_after_it():
    section_holder = []

    def guidance(shown):
        section_holder.append(_section(Role.PLANNER, list(shown)))
        return section_holder[-1]

    graph = "GRAPH " * 200
    base = RequestCapacity(tokens=0)
    fixed = base.count("SYS") + base.count("HEAD\n") + base.count("SKILLS\n") + base.count("\nSUFFIX")
    capacity = RequestCapacity(tokens=fixed + base.count(graph) + 20)
    prompt, details = _planner(capacity, guidance, graph, lambda b: (graph, _package(["A.java"])),
                               reference="[Source: x]\nREFERENCE TEXT " * 30)
    assert graph in prompt, "the graph context is fitted first"
    assert details["capability_guidance"]["used_tokens"] <= 20
    assert details["reference"]["omitted"], "the reference gets only what guidance leaves"


# -- the Reviewer -----------------------------------------------------------------


def test_review_guidance_is_dropped_before_any_file_is_omitted():
    sent = []
    reviewer = SimpleNamespace(role_llm=None, max_output_tokens=None, _candidates=lambda: [None])
    files = [("A.java", "class A {}\n" * 40)]
    file_tokens = RequestCapacity(tokens=0).count("=== A.java ===\n" + files[0][1])

    def requests(tokens):
        capacity = RequestCapacity(tokens=tokens)
        with patch.object(budget, "agent_request_capacity", lambda *a, **k: capacity), \
                patch.object(budget, "candidate_request_capacity", lambda *a, **k: capacity):
            batches, truncated, _fit = budget.review_requests(
                SimpleNamespace(), reviewer, files, "SYS", "HEADER\n",
                guidance=lambda candidate, number: _section(Role.DEVELOPER, ["A.java"],
                                                            lambda f, b: sent.append(b)))
            return batches[0].first(), truncated

    roomy, truncated = requests(100_000)
    assert "[java]" in roomy and not truncated and roomy.startswith("HEADER\n\n\n=== Kriya capability guidance")
    sent.clear()
    tight, truncated = requests(RequestCapacity(tokens=0).count("SYS") + RequestCapacity(tokens=0).count(
        "HEADER\n") + RequestCapacity(tokens=0).count(budget.REVIEW_BATCH_LABEL_BOUND) + file_tokens + 40)
    assert "class A" in tight and not truncated
    assert "[java]" not in tight and sent[-1].text == "" and sent[-1].dropped_by_fit_rule_ids


# -- structure -------------------------------------------------------------------


def test_no_new_fitting_function():
    """Guidance is fitted only through fit_variable_section (directly or via
    fit_developer_request): the integration module defines no fitter."""
    path = os.path.join(os.path.dirname(budget.__file__), "capability_guidance.py")
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not {name for name in names if name.startswith("fit")}
    source = ast.unparse(ast.parse(open(budget.__file__, encoding="utf-8").read()))
    assert "def fit_guidance_section" in source and "fit_variable_section(capacity, fixed_texts, build)" in source


# -- the workflow-side facts helpers -------------------------------------------


def test_run_repository_facts_normal_output(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    facts = cg.run_repository_facts(str(tmp_path), frameworks=["Spring Boot"], dependency_graph_path=None,
                                    goal="Add a cache. Do not use Gradle.")
    assert facts == RepositoryFacts(True, False, (BuildRootFact("", frozenset({"maven"})),), frozenset())
    greenfield = cg.run_repository_facts(str(tmp_path / "missing-dir-is-an-error"), frameworks=[],
                                         dependency_graph_path=None, goal="A Maven project")
    assert greenfield is cg.UNKNOWN_REPOSITORY_FACTS


def test_run_repository_facts_goal_build_systems_for_greenfield(tmp_path):
    facts = cg.run_repository_facts(str(tmp_path), frameworks=[], dependency_graph_path=None,
                                    goal="In a Maven project, print hello. Do not use Gradle.")
    assert facts.greenfield and facts.goal_build_systems == {"maven"}


def test_developer_operation_mapping(tmp_path):
    (tmp_path / "A.java").write_text("class A {}")
    assert cg.developer_operation(1, ["A.java", "B.java"], str(tmp_path)) is Operation.EDIT
    assert cg.developer_operation(1, ["B.java"], str(tmp_path)) is Operation.CREATE
    assert cg.developer_operation(1, [], str(tmp_path)) is Operation.CREATE
    assert cg.developer_operation(2, ["B.java"], str(tmp_path)) is Operation.REPAIR


def test_planner_request_never_exceeds_its_capacity_with_guidance_and_reference():
    """Each section is fitted into the room the sections ranked above it
    leave: graph, then guidance, then the reference."""
    graph = "GRAPH " * 200
    reference = "".join(f"\n[Source: s{i}]\nREFERENCE TEXT {i} " * 3 for i in range(20))
    base = RequestCapacity(tokens=0)
    for extra in range(0, 400, 7):
        tokens = base.count("SYS") + base.count("HEAD\n") + base.count("SKILLS\n") + base.count("\nSUFFIX") + \
            base.count(graph) + extra
        capacity = RequestCapacity(tokens=tokens)
        prompt, _ = _planner(capacity, lambda shown: _section(Role.PLANNER, list(shown)), graph,
                             lambda b: (graph, _package(["A.java"])), reference=reference)
        assert capacity.count("SYS") + capacity.count(prompt) <= tokens


def test_a_guidance_only_reduction_is_reported():
    graph = "GRAPH\n"
    base = RequestCapacity(tokens=0)
    section = _section(Role.PLANNER, ["A.java"])
    tokens = base.count("SYS") + base.count("HEAD\n") + base.count("SKILLS\n") + base.count("\nSUFFIX") + \
        base.count(graph) + section.base.estimated_tokens - 15
    prompt, details = _planner(RequestCapacity(tokens=tokens), lambda shown: _section(Role.PLANNER, list(shown)),
                               graph, lambda b: (graph, _package(["A.java"])))
    assert graph in prompt and details["capability_guidance"]["builds"] >= 1
    assert details["graph"]["builds"] == 1 and details["reference"] is None
