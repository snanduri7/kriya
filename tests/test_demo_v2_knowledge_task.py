"""Demo V2 scenario 02, REJECTED candidate 1 (count_top_level_functions; its no-skill baseline passed, see the pre-registration addendum): the isolated offline-reuse task is a fair, deterministic discriminator.

No model, no network. Real pytest runs of the fixture's own tests against hand-written implementations: the current-API reference must pass, and each obsolete or
non-conforming construction must fail the way it is predicted to. A task that every variant passed, or that leaks the API fact, would not demonstrate knowledge reuse.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SC = ROOT / "demo-v2" / "scenarios" / "02-knowledge-skill"
FIXTURE = ROOT / "demo-v2" / "fixtures" / "knowledge-skill-c1-rejected" / "repo"
GOAL = SC / "goal.candidate-1-rejected.md"

REFERENCE = '''
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

_PARSER = Parser(Language(tspython.language()))


def count_top_level_functions(source: str) -> int:
    tree = _PARSER.parse(source.encode("utf8"))
    return sum(1 for node in tree.root_node.children if node.type == "function_definition")
'''

# Constructions from before py-tree-sitter 0.22 (the library the goal pins is 0.25.2): each must raise at construction time on 0.25.2.
OBSOLETE_TWO_ARG_LANGUAGE = REFERENCE.replace("Language(tspython.language())", 'Language(tspython.language(), "python")')
OBSOLETE_SET_LANGUAGE = REFERENCE.replace(
    "_PARSER = Parser(Language(tspython.language()))", "_PARSER = Parser()\n_PARSER.set_language(Language(tspython.language()))")
OBSOLETE_BUILD_LIBRARY = REFERENCE.replace(
    "_PARSER = Parser(Language(tspython.language()))", 'Language.build_library("build/x.so", ["vendor/tree-sitter-python"])\n_PARSER = Parser()')

# The standard-library parser the goal forbids, made as tolerant as it can be: it cannot recover functions around a syntax error.
AST_VARIANT = '''
import ast


def count_top_level_functions(source: str) -> int:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    return sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in tree.body)
'''

# Text matching passes every behavioural case except the one that proves the named parser library is used.
REGEX_VARIANT = '''
import re


def count_top_level_functions(source: str) -> int:
    return len(re.findall(r"^(?:async\\s+)?def\\s", source, flags=re.M))
'''

BEHAVIOURAL = {"test_function_is_defined", "test_no_functions", "test_empty_source", "test_two_top_level_functions", "test_async_function_counts",
               "test_nested_functions_and_methods_are_not_counted", "test_syntax_error_does_not_raise_and_recognisable_functions_are_counted"}
ALL_TESTS = BEHAVIOURAL | {"test_the_parser_library_named_in_the_goal_is_used"}


def _run(production: str, tmp_path: Path) -> tuple[set[str], set[str], str]:
    """Run the fixture's tests against `production` as outline.py. Returns (passed, failed, output) test names."""
    work = tmp_path / "w"
    work.mkdir(parents=True)
    (work / "outline.py").write_text(production, encoding="utf-8")
    (work / "test_outline.py").write_text((FIXTURE / "test_outline.py").read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--tb=line", "-rA"], cwd=work, capture_output=True, text=True, check=False,
                            env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
    passed = set(re.findall(r"^PASSED test_outline.py::(\w+)", result.stdout, flags=re.M))
    failed = set(re.findall(r"^FAILED test_outline.py::(\w+)", result.stdout, flags=re.M))
    return passed, failed, result.stdout + result.stderr


def test_the_stub_is_a_clean_failed_and_passed_split_not_a_collection_error(tmp_path):
    passed, failed, out = _run((FIXTURE / "outline.py").read_text(encoding="utf-8"), tmp_path)
    assert passed == {"test_function_is_defined"}, out
    assert failed == ALL_TESTS - passed, out


def test_the_current_api_reference_passes_everything(tmp_path):
    passed, failed, out = _run(REFERENCE, tmp_path)
    assert failed == set() and passed == ALL_TESTS, out


def test_each_obsolete_construction_fails_on_the_pinned_version_with_a_library_error(tmp_path):
    for name, variant, error in (("two_arg_language", OBSOLETE_TWO_ARG_LANGUAGE, "TypeError"), ("set_language", OBSOLETE_SET_LANGUAGE, "AttributeError"),
                                 ("build_library", OBSOLETE_BUILD_LIBRARY, "AttributeError")):
        passed, failed, out = _run(variant, tmp_path / name)
        # the module fails at import: no test passes, and the failure is raised by the library, not by a logic slip in the test inputs
        assert passed == set() and failed == set() and "ERROR" in out and error in out, (name, out)


def test_the_stdlib_ast_variant_fails_exactly_where_the_goal_forbids_it(tmp_path):
    passed, failed, out = _run(AST_VARIANT, tmp_path)
    assert failed == {"test_syntax_error_does_not_raise_and_recognisable_functions_are_counted", "test_the_parser_library_named_in_the_goal_is_used"}, out
    assert passed == ALL_TESTS - failed


def test_a_text_matching_variant_cannot_pass_without_the_named_library(tmp_path):
    passed, failed, out = _run(REGEX_VARIANT, tmp_path)
    assert "test_the_parser_library_named_in_the_goal_is_used" in failed, out


def test_the_syntax_error_input_has_a_stable_recoverable_count_in_the_installed_library(tmp_path):
    # the test's expected value (2) is a measured property of tree-sitter 0.25.2, pinned here so a library change shows up as this failure, not as a mystery in a demo run
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser
    source = re.search(r'SYNTAX_ERROR_SOURCE = "(.*)"', (FIXTURE / "test_outline.py").read_text(encoding="utf-8")).group(1).encode().decode("unicode_escape")
    tree = Parser(Language(tspython.language())).parse(source.encode("utf8"))
    assert tree.root_node.has_error
    assert [node.type for node in tree.root_node.children].count("function_definition") == 2


def test_the_task_does_not_leak_the_api_fact_into_anything_the_model_can_read():
    forbidden = ("Parser", "Language", "language()", "root_node", "set_language", "tspython", "tree_sitter_python", "build_library", ".parse(", "children", "node.type")
    readable = [GOAL, *(p for p in FIXTURE.iterdir() if p.is_file())]
    assert {p.name for p in readable} == {"goal.candidate-1-rejected.md", "README.md", "requirements.txt", "outline.py", "test_outline.py"}
    for path in readable:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, (path.name, token)
    # the goal still carries what KnowledgeGuard needs (pins) and the library the tests check, and requirements.txt matches the pins
    goal = GOAL.read_text(encoding="utf-8")
    assert "tree-sitter==0.25.2" in goal and "tree-sitter-python==0.25.0" in goal
    assert "tree-sitter==0.25.2" in (FIXTURE / "requirements.txt").read_text(encoding="utf-8")


def test_the_rejected_complex_task_is_kept_and_labelled_not_used():
    old = (SC / "goal.rejected-complex-task.md").read_text(encoding="utf-8")
    assert old.startswith("<!-- SCENARIO-B-COMPLEX-TASK-NOT-DEMO-SUITABLE")
    assert old != GOAL.read_text(encoding="utf-8")
