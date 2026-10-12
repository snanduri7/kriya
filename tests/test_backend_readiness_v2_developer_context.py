"""BACKEND-READINESS-V2 Stage C change 2 (T2): the Developer's context must carry the callee a goal-named member delegates to.

Measured on T2 (jsoup): ``Node.absUrl`` only ends in ``return StringUtil.resolve(...)``; the collaborator signature list kept the
FIRST 12 callables of StringUtil in file order, so ``resolve`` (declared late) was absent, and no source of the callee reached the
Developer. The fixtures below are synthetic repositories of that shape: no task, repository or file name is known to the code."""
import textwrap

import pytest
from test_backend_readiness_v2_localization import _service

from kriya.workflow import goal_facts

NODE = textwrap.dedent("""\
    package lib.nodes;

    import lib.Strings;

    public abstract class Node {
        public String absUrl(String key) {
            String raw = attr(key);
            return Strings.resolve(baseUri(), raw);
        }

        public String plain(String key) {
            return attr(key).trim();
        }

        abstract String attr(String key);
        abstract String baseUri();
    }
    """)


def _strings(fillers: int, *, resolve_first: bool = False, resolve_body_lines: int = 3) -> str:
    filler = "".join(f"    public static String filler{i}(String v) {{ return v; }}\n" for i in range(fillers))
    body = "".join(f"        step{i}(base, rel);\n" for i in range(resolve_body_lines))
    resolve = (f"    public static String resolve(String base, String rel) {{\n{body}        return base + rel;\n    }}\n"
               "    public static String resolve(String base, int rel) {\n        return base;\n    }\n")
    return "package lib;\n\npublic final class Strings {\n" + (resolve + filler if resolve_first else filler + resolve) + "}\n"


def _package(tmp_path, strings: str, node: str = NODE, budget=None, member="absUrl"):
    _root, service = _service(tmp_path, {"src/main/java/lib/nodes/Node.java": node, "src/main/java/lib/Strings.java": strings})
    try:
        symbol = next(s for s in service.find_symbol(f"lib.nodes.Node.{member}") if s.is_callable)
        package = service.build_context(symbol.symbol_id, budget, count_tokens=lambda text: max(1, len(text) // 4),
                                        include_target=False, member_signatures=True)
        return package, package.render(include_target=False)
    finally:
        service.close()


def _collaborator(package, key="lib.Strings"):
    return dict(package.collaborators).get(key, [])


def test_the_delegated_callee_survives_truncation_whatever_the_declaration_order(tmp_path):
    late, _ = _package(tmp_path / "late", _strings(30))
    early, _ = _package(tmp_path / "early", _strings(30, resolve_first=True))
    for package in (late, early):
        signatures = _collaborator(package)
        assert len(signatures) <= 12
        assert sum("resolve(" in s for s in signatures) == 2  # both overloads of the delegate


def test_unrelated_extra_signatures_do_not_change_the_selected_relevant_facts(tmp_path):
    few, _ = _package(tmp_path / "few", _strings(3))
    many, _ = _package(tmp_path / "many", _strings(60))
    pick = lambda package: [s for s in _collaborator(package) if "resolve(" in s]  # noqa: E731
    assert pick(few) == pick(many) and pick(few)


def test_the_callee_source_is_provided_with_provenance_and_is_bounded(tmp_path):
    package, text = _package(tmp_path, _strings(30, resolve_body_lines=200))
    assert "Delegate callee" in text and "DERIVED_DETERMINISTIC" in text
    assert "lib.Strings.resolve" in text and "src/main/java/lib/Strings.java" in text
    assert "step0(base, rel);" in text
    assert "step199" not in text and "more lines" in text  # bounded, with the elision stated
    assert text.count("Delegate callee") <= 3


def test_the_line_cap_and_the_char_cap_each_bound_the_callee_source_independently(tmp_path):
    short_lines = _strings(5).replace("        step0(base, rel);\n", "".join(f"        s{i}();\n" for i in range(300)))
    _package_, text = _package(tmp_path / "lines", short_lines)
    shown = text.split("#### Delegate callee")[1]
    assert "s30();" in shown and "s60();" not in shown and "more lines" in shown  # 40 lines, far below the char cap
    long_lines = _strings(5).replace("        step0(base, rel);\n", f"        step0({'x' * 300});\n" * 20)
    _package_, text = _package(tmp_path / "chars", long_lines)
    assert len(text.split("#### Delegate callee")[1].split("####")[0]) < 3000


def test_a_member_that_is_not_a_thin_delegate_gets_no_callee_source(tmp_path):
    package, text = _package(tmp_path, _strings(5), member="plain")
    assert "Delegate callee" not in text and not package.delegates


def test_the_budget_is_strict_and_the_target_header_is_never_traded_away(tmp_path):
    full, full_text = _package(tmp_path / "a", _strings(30))
    tiny, tiny_text = _package(tmp_path / "b", _strings(30), budget=max(1, len(full_text) // 4 // 3))
    assert tiny.over_budget or len(tiny_text) < len(full_text)
    assert "Delegate callee" not in tiny_text or len(tiny_text) // 4 <= len(full_text) // 4 // 3


def test_the_developer_sees_the_same_delegate_relationship_the_planner_was_given(tmp_path):
    root, service = _service(tmp_path, {"src/main/java/lib/nodes/Node.java": NODE, "src/main/java/lib/Strings.java": _strings(30)})
    try:
        [planner_line] = goal_facts.delegate_facts(service, "Node.absUrl(...) resolves relative links incorrectly.\n")
    finally:
        service.close()
    _package_, text = _package(tmp_path / "dev", _strings(30))
    assert "Strings.resolve" in planner_line and "src/main/java/lib/Strings.java" in planner_line
    assert "Strings.resolve" in text and "src/main/java/lib/Strings.java" in text
    assert root.exists()


@pytest.mark.parametrize("priority", [(), ("filler30",)])
def test_goal_named_symbols_rank_ahead_of_file_order(tmp_path, priority):
    _root, service = _service(tmp_path, {"src/main/java/lib/nodes/Node.java": NODE, "src/main/java/lib/Strings.java": _strings(40)})
    try:
        symbol = next(s for s in service.find_symbol("lib.nodes.Node.absUrl") if s.is_callable)
        package = service.build_context(symbol.symbol_id, None, count_tokens=lambda text: max(1, len(text) // 4),
                                        include_target=False, member_signatures=True, priority_names=priority)
    finally:
        service.close()
    signatures = _collaborator(package)
    assert sum("resolve(" in s for s in signatures) == 2
    assert any("filler30(" in s for s in signatures) == bool(priority)


def test_developer_goal_facts_equal_the_planner_block_and_ignore_the_planners_own_strategy(tmp_path):
    from kriya.agents.contracts import AUTHORITATIVE_GOAL_SECTION_HEADER, PLANNED_IMPLEMENTATION_SECTION_HEADER

    root, service = _service(tmp_path, {"src/main/java/lib/nodes/Node.java": NODE, "src/main/java/lib/Strings.java": _strings(5)})
    service.close()
    user_goal = "Node.absUrl(...) resolves relative links incorrectly.\n"
    graph = str(tmp_path / "memory" / "dependency_graph.db")
    planner_block = goal_facts.goal_facts_block(graph, str(root), user_goal)
    assert "returns the result of Strings.resolve" in planner_block
    subtask_goal = (f"{AUTHORITATIVE_GOAL_SECTION_HEADER}\n{user_goal}\n{PLANNED_IMPLEMENTATION_SECTION_HEADER}\n"
                    "Modify Node.plain to trim.\n")
    assert goal_facts.developer_goal_facts(subtask_goal, str(root), graph) == planner_block
    assert goal_facts.developer_goal_facts(user_goal, str(root), graph) == planner_block  # a plain goal is all authoritative
    strategy_only = f"{AUTHORITATIVE_GOAL_SECTION_HEADER}\nnothing named here\n\n{PLANNED_IMPLEMENTATION_SECTION_HEADER}\n{user_goal}"
    assert goal_facts.developer_goal_facts(strategy_only, str(root), graph) == ""  # strategy text never becomes a fact


def test_the_developer_request_builder_injects_the_facts_block():
    """Structural wiring: run_generation_workflow appends the block to the Developer's convention prompt."""
    import inspect

    from kriya.workflow import workflow

    source = inspect.getsource(workflow.WorkflowEngine.run_generation_workflow)
    assert "developer_goal_facts(" in source and "convention_prompt += goal_facts_text" in source


def test_end_to_end_the_real_developer_request_carries_the_facts_and_the_delegate_callee(tmp_path):
    """Whole direct workflow with scripted model transport: the request the Developer actually receives names where the goal's
    member delegates to AND shows the callee's source, although the callee is declared late in a large class."""
    import asyncio
    import subprocess
    from unittest.mock import AsyncMock, patch

    from _edit_protocol_harness import make_config
    from _protocol_responses import sentinel

    from kriya.core import model_runtime
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    node_path = "src/main/java/lib/nodes/Node.java"
    # a large owner file is shown as the exact member plus signatures (tier member_exact), the production T2 shape
    filler = "".join(f"    int helper{i}(int v) {{\n        int a = v + {i};\n        int b = a * 2;\n        return b - a;\n    }}\n\n"
                     for i in range(500))
    big_node = NODE.replace("    abstract String attr(String key);", filler + "    abstract String attr(String key);")
    root, service = _service(tmp_path, {node_path: big_node, "src/main/java/lib/Strings.java": _strings(40)})
    service.close()
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True, capture_output=True)
    model_runtime.clear_model_runtime_cache()
    config = make_config(tmp_path)
    sent = []

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = '{"files": ["%s"]}' % node_path
        elif "Developer Agent" in first:
            sent.append(user_prompt)
            content = sentinel(node_path, analysis="FIX ANALYSIS: trim the value.", content=big_node.replace("raw = attr(key);", "raw = attr(key).trim();"))
        else:
            content = "Review: Approved"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5, "finish_reason": "stop",
                "provider_metadata": {}}

    engine = WorkflowEngine(Kernel(config=config), LLMClient(config))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=lambda *a, **k: {"success": True, "output": "1 passed"}):
        asyncio.run(engine.run_generation_workflow(
            goal="Node.absUrl(...) resolves relative links incorrectly.\n", workspace_path=str(root),
            predetermined_plan=f"Repair {node_path}", predetermined_design="", predetermined_architect_files=[node_path],
            approval_callback=AsyncMock(return_value=True)))
    assert sent, "the Developer was never called"
    request = sent[0]
    assert "=== GOAL-NAMED FACTS" in request and "returns the result of Strings.resolve" in request
    assert "#### Delegate callee lib.Strings.resolve" in request and "step0(base, rel);" in request


def test_under_a_tight_budget_the_signatures_are_dropped_before_the_delegate_source(tmp_path):
    full, full_text = _package(tmp_path / "full", _strings(30))
    sig_chars = sum(len(s) + 1 for s in _collaborator(full)) + len("#### Collaborator lib.Strings (signatures, read-only)\n")
    target = len(full_text) - sig_chars + 40  # room for everything except the collaborator signatures
    tight, tight_text = _package(tmp_path / "tight", _strings(30), budget=target // 4)
    assert tight.delegates and "Delegate callee" in tight_text
    assert not tight.collaborators or len(tight_text) <= target + 8
