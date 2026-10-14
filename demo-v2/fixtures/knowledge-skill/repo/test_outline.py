"""Behavioural tests only: they call the public function and check its result."""
import sys

import outline


def encode(source):
    return source.encode("utf-16-le")


def test_function_is_defined():
    assert callable(outline.function_start_offsets)


def test_no_functions():
    assert outline.function_start_offsets(encode("x = 1\nprint(x)\n")) == []


def test_empty_source():
    assert outline.function_start_offsets(b"") == []


def test_two_top_level_functions():
    source = "def a():\n    pass\n\ndef b():\n    pass\n"
    assert outline.function_start_offsets(encode(source)) == [0, 2 * source.index("def b")]


def test_async_function_starts_at_its_async_keyword():
    source = "x = 1\nasync def f():\n    pass\n"
    assert outline.function_start_offsets(encode(source)) == [2 * source.index("async")]


def test_non_ascii_text_before_a_function_shifts_the_offset_by_two_bytes_per_character():
    source = "# caf\u00e9 \u2603\ndef g():\n    pass\n"
    assert outline.function_start_offsets(encode(source)) == [2 * source.index("def g")] == [18]


def test_nested_functions_and_methods_are_not_included():
    source = "def outer():\n    def inner():\n        pass\n\nclass C:\n    def method(self):\n        pass\n"
    assert outline.function_start_offsets(encode(source)) == [0]


def test_the_parser_library_named_in_the_goal_is_used():
    outline.function_start_offsets(encode("x = 1\n"))
    assert "tree_sitter" in sys.modules
