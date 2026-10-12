"""BACKEND-READINESS-V2, T2: a goal-named member whose value is computed by another project method.

Measured (jsoup, T2): the goal names ``Element.absUrl``; its declaration only ends in ``return StringUtil.resolve(...)``.
Two candidates were written into the delegate (one broke eight existing tests, one fixed half of it) and the Planner listed the
callee as a preserved reference. The fixtures are small synthetic repositories of that shape (no task, repository or file name
is known to the code under test)."""
import textwrap

import pytest
from test_backend_readiness_v2_localization import GOAL, PATHS, _service

from kriya.workflow import goal_facts


def _facts(tmp_path, paths, goal=GOAL):
    _root, service = _service(tmp_path, paths)
    try:
        return goal_facts.delegate_facts(service, goal)
    finally:
        service.close()


def _with_node(body: str):
    node = textwrap.dedent(f"""\
        package lib.nodes;

        public abstract class Node extends Base {{
            public String absUrl(String key) {{
        {textwrap.indent(textwrap.dedent(body), "        ")}
            }}
        }}
        """)
    return {**PATHS, "src/main/java/lib/nodes/Node.java": node}


def test_the_delegate_of_the_declaring_member_is_named_with_its_location(tmp_path):
    [line] = _facts(tmp_path, PATHS)
    assert line == ("lib.nodes.Node.absUrl (src/main/java/lib/nodes/Node.java:8) returns the result of Strings.resolve(...); "
                    "that method is declared at src/main/java/lib/Strings.java:4")


def test_overloaded_callee_lists_every_declaration_up_to_three(tmp_path):
    strings = ("package lib;\n\npublic final class Strings {\n"
               + "".join(f"    public static String resolve({t} v) {{ return \"\"; }}\n" for t in ("String", "int", "long", "char", "byte"))
               + "}\n")
    [line] = _facts(tmp_path, {**PATHS, "src/main/java/lib/Strings.java": strings})
    assert line.count("src/main/java/lib/Strings.java:") == 3 and line.endswith("(+2 more)")


@pytest.mark.parametrize("body", [
    'return "";',                                     # no delegation
    "return key.trim();",                             # an instance call
    "return String.valueOf(key);",                    # a type the project does not declare
    "return Missing.resolve(key);",                   # an unknown type
    "return Strings.nothingLikeThis(key);",           # a method the type does not declare
    'if (key == null) { return Strings.resolve("x"); }\nreturn "";',  # the LAST return decides
    "// return Strings.resolve(key);\nreturn \"\";",    # comments are not code
    'String s = "return Strings.resolve(key);";\nreturn s;',  # strings are not code
])
def test_no_fact_unless_the_last_return_calls_a_project_method(tmp_path, body):
    assert _facts(tmp_path, _with_node(body)) == []


def test_the_last_return_is_the_one_that_counts(tmp_path):
    body = 'if (key == null) { return ""; }\nreturn Strings.resolve(key);'
    assert len(_facts(tmp_path, _with_node(body))) == 1


def test_an_overloaded_goal_name_is_not_an_anchor_and_a_callee_in_the_same_file_is_not_a_delegate(tmp_path):
    # Entry.parse has fifteen overloads: the name does not say which declaration the goal means.
    overloaded_goal = "Entry.parse(...) returns the wrong thing."
    entry = PATHS["src/main/java/lib/Entry.java"].replace("{ return null; }", "{ return Strings.resolve(\"\"); }")
    assert _facts(tmp_path, {**PATHS, "src/main/java/lib/Entry.java": entry}, overloaded_goal) == []
    same_file = textwrap.dedent("""\
        package lib.nodes;

        public abstract class Node extends Base {
            public String absUrl(String key) {
                return Node.helper(key);
            }

            public static String helper(String key) {
                return key;
            }
        }
        """)
    assert _facts(tmp_path / "b", {**PATHS, "src/main/java/lib/nodes/Node.java": same_file}) == []


def test_a_name_the_goal_does_not_state_gives_nothing_and_python_is_out_of_scope(tmp_path):
    assert _facts(tmp_path, PATHS, "Element.text() is wrong.") == []
    assert _facts(tmp_path / "b", PATHS, "Nowhere.absUrl() is wrong.") == []


def test_the_block_renders_the_delegate_line_and_an_empty_block_stays_empty(tmp_path):
    root, service = _service(tmp_path)
    try:
        block = goal_facts.render_goal_facts(goal_facts.build_goal_facts(GOAL, str(root), service))
        assert "returns the result of Strings.resolve(...)" in block
        facts = goal_facts.GoalFacts(delegates=["x returns the result of Y.z(...); that method is declared at a:1"])
        assert facts.empty() is False and "- x returns the result of Y.z" in goal_facts.render_goal_facts(facts)
        assert goal_facts.GoalFacts().empty() is True
    finally:
        service.close()


def test_a_field_or_a_trailing_comment_is_not_a_delegation(tmp_path):
    field_only = "package lib;\n\npublic final class Strings {\n    public static String resolve = \"x\";\n}\n"
    assert _facts(tmp_path, {**PATHS, "src/main/java/lib/Strings.java": field_only}) == []
    assert _facts(tmp_path / "b", _with_node('return "";\n// return Strings.resolve(key);')) == []


def test_the_number_of_facts_is_bounded(tmp_path):
    classes = {f"src/main/java/lib/A{i}.java": f"package lib;\n\npublic class A{i} {{\n    public String go() {{\n        return Strings.resolve(\"x\");\n    }}\n}}\n"
               for i in range(7)}
    goal = " ".join(f"A{i}.go() is wrong." for i in range(7))
    assert len(_facts(tmp_path, {**PATHS, **classes}, goal)) == goal_facts._MAX_DELEGATE_FACTS  # noqa: SLF001 - the bound under test


def test_only_java_declarations_are_read(tmp_path):
    python = {"lib/node.py": "class Node:\n    def absUrl(self, key):\n        return Strings.resolve(key)\n",
              "lib/strings.py": "class Strings:\n    @staticmethod\n    def resolve(key):\n        return key\n"}
    assert _facts(tmp_path, python, "Node.absUrl() is wrong.") == []
