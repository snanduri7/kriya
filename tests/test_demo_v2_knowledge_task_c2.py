"""Demo V2 scenario 02, ACTIVE candidate 2 (function_start_offsets over UTF-16 bytes): a fair, deterministic discriminator. No model, no network.

Real pytest runs of the fixture's own tests against hand-written implementations. The pre-registered honesty clause: the task can ALSO be solved without the encoding fact by decoding the
bytes and converting offsets, so one variant that does exactly that must pass; a task that nothing but the acquired rule could solve would be a different (and false) claim.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SC = ROOT / "demo-v2" / "scenarios" / "02-knowledge-skill"
FIXTURE = ROOT / "demo-v2" / "fixtures" / "knowledge-skill" / "repo"

HEADER = '''
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

_PARSER = Parser(Language(tspython.language()))
'''
REFERENCE = HEADER + '''

def function_start_offsets(data: bytes) -> list[int]:
    tree = _PARSER.parse(data, encoding="utf16")
    return [node.start_byte for node in tree.root_node.children if node.type == "function_definition"]
'''
# decode, parse as UTF-8, convert each UTF-8 byte offset back to a character index, then to UTF-16 bytes: correct without knowing the encoding parameter
DECODE_AND_CONVERT = HEADER + '''

def function_start_offsets(data: bytes) -> list[int]:
    text = data.decode("utf-16-le")
    raw = text.encode("utf8")
    tree = _PARSER.parse(raw)
    return [2 * len(raw[:node.start_byte].decode("utf8")) for node in tree.root_node.children if node.type == "function_definition"]
'''
# the tempting shortcut: UTF-8 offset times two (right for ASCII, wrong once a multi-byte character precedes a function)
DOUBLE_THE_UTF8_OFFSET = HEADER + '''

def function_start_offsets(data: bytes) -> list[int]:
    tree = _PARSER.parse(data.decode("utf-16-le").encode("utf8"))
    return [2 * node.start_byte for node in tree.root_node.children if node.type == "function_definition"]
'''
# the UTF-16 bytes handed to the default (UTF-8) parse
RAW_BYTES_AS_UTF8 = HEADER + '''

def function_start_offsets(data: bytes) -> list[int]:
    tree = _PARSER.parse(data)
    return [node.start_byte for node in tree.root_node.children if node.type == "function_definition"]
'''
OBSOLETE_SET_LANGUAGE = REFERENCE.replace("_PARSER = Parser(Language(tspython.language()))", "_PARSER = Parser()\n_PARSER.set_language(Language(tspython.language()))")
OBSOLETE_TWO_ARG_LANGUAGE = REFERENCE.replace("Language(tspython.language())", 'Language(tspython.language(), "python")')
AST_VARIANT = '''
import ast


def function_start_offsets(data: bytes) -> list[int]:
    text = data.decode("utf-16-le")
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    return [2 * (starts[node.lineno - 1] + node.col_offset) for node in ast.parse(text).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
'''

ALL_TESTS = {"test_function_is_defined", "test_no_functions", "test_empty_source", "test_two_top_level_functions", "test_async_function_starts_at_its_async_keyword",
             "test_non_ascii_text_before_a_function_shifts_the_offset_by_two_bytes_per_character", "test_nested_functions_and_methods_are_not_included",
             "test_the_parser_library_named_in_the_goal_is_used"}
NON_ASCII = "test_non_ascii_text_before_a_function_shifts_the_offset_by_two_bytes_per_character"
LIBRARY = "test_the_parser_library_named_in_the_goal_is_used"


def _run(production: str, tmp_path: Path) -> tuple[set[str], set[str], str]:
    work = tmp_path / "w"
    work.mkdir(parents=True)
    (work / "outline.py").write_text(production, encoding="utf-8")
    (work / "test_outline.py").write_text((FIXTURE / "test_outline.py").read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--tb=line", "-rA"], cwd=work, capture_output=True, text=True, check=False,
                            env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
    passed = set(re.findall(r"^PASSED test_outline.py::(\w+)", result.stdout, flags=re.M))
    failed = set(re.findall(r"^FAILED test_outline.py::(\w+)", result.stdout, flags=re.M))
    return passed, failed, result.stdout + result.stderr


def test_the_stub_is_a_clean_failed_and_passed_split(tmp_path):
    passed, failed, out = _run((FIXTURE / "outline.py").read_text(encoding="utf-8"), tmp_path)
    assert passed == {"test_function_is_defined"} and failed == ALL_TESTS - passed, out


def test_the_current_api_reference_passes_everything(tmp_path):
    passed, failed, out = _run(REFERENCE, tmp_path)
    assert failed == set() and passed == ALL_TESTS, out


def test_a_solution_that_never_uses_the_encoding_parameter_can_also_pass(tmp_path):
    # the honest limit of this task, stated by a test: decoding first is a legitimate way to solve it
    passed, failed, out = _run(DECODE_AND_CONVERT, tmp_path)
    assert failed == set() and passed == ALL_TESTS, out


def test_the_ascii_only_shortcut_fails_exactly_the_non_ascii_case(tmp_path):
    passed, failed, out = _run(DOUBLE_THE_UTF8_OFFSET, tmp_path)
    assert failed == {NON_ASCII}, out
    assert passed == ALL_TESTS - failed


def test_feeding_utf16_bytes_to_the_default_parse_fails_every_content_case(tmp_path):
    passed, failed, out = _run(RAW_BYTES_AS_UTF8, tmp_path)
    assert {"test_two_top_level_functions", "test_async_function_starts_at_its_async_keyword", NON_ASCII} <= failed, out


def test_obsolete_constructions_fail_at_import_with_a_library_error(tmp_path):
    for name, variant, error in (("set_language", OBSOLETE_SET_LANGUAGE, "AttributeError"), ("two_arg", OBSOLETE_TWO_ARG_LANGUAGE, "TypeError")):
        passed, failed, out = _run(variant, tmp_path / name)
        assert passed == set() and failed == set() and "ERROR" in out and error in out, (name, out)


def test_the_stdlib_ast_variant_is_caught_only_by_the_library_use_test(tmp_path):
    passed, failed, out = _run(AST_VARIANT, tmp_path)
    assert failed == {LIBRARY}, out
    assert passed == ALL_TESTS - failed


def test_expected_offsets_in_the_tests_equal_the_installed_librarys_offsets(tmp_path):
    # the expectations are computed from character positions (2 bytes each); the library with encoding="utf16" must agree, so a library change shows up here
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser
    parser = Parser(Language(tspython.language()))
    for source, needle in (("def a():\n    pass\n\ndef b():\n    pass\n", "def b"), ("x = 1\nasync def f():\n    pass\n", "async"), ("# café ☃\ndef g():\n    pass\n", "def g")):
        starts = [n.start_byte for n in parser.parse(source.encode("utf-16-le"), encoding="utf16").root_node.children if n.type == "function_definition"]
        assert 2 * source.index(needle) in starts


def test_the_task_does_not_leak_the_api_fact_into_anything_the_model_can_read():
    forbidden = ("Parser", "Language", "language()", "root_node", "set_language", "tspython", "tree_sitter_python", "build_library", ".parse(", "children", "node.type",
                 "encoding=", "utf16", "start_byte")
    readable = [SC / "goal.md", *(p for p in FIXTURE.iterdir() if p.is_file())]
    assert {p.name for p in readable} == {"goal.md", "README.md", "requirements.txt", "outline.py", "test_outline.py"}
    for path in readable:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, (path.name, token)
    goal = (SC / "goal.md").read_text(encoding="utf-8")
    assert "tree-sitter==0.25.2" in goal and "tree-sitter-python==0.25.0" in goal and "UTF-16 little-endian" in goal
    assert "tree-sitter-python==0.25.0" in (FIXTURE / "requirements.txt").read_text(encoding="utf-8")


def test_the_rule_this_task_depends_on_is_in_the_preserved_acquired_skill():
    rules = (ROOT / "handover" / "DEMO-V2" / "evidence" / "scenario-02-live-retry-1" / "acquired-skills" / "tree-sitter-python" / "rules.txt").read_text(encoding="utf-8")
    assert "the encoding parameter in the parse() function must be either 'utf8' or 'utf16'" in rules
    assert "tree_sitter_python.language()" in rules
