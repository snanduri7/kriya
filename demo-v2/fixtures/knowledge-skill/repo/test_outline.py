"""Behavioural tests only: they call the public function and check its result."""
import sys

import outline

SYNTAX_ERROR_SOURCE = "def first():\n    pass\nx = = 3\ndef second():\n    pass\n"


def test_function_is_defined():
    assert callable(outline.count_top_level_functions)


def test_no_functions():
    assert outline.count_top_level_functions("x = 1\nprint(x)\n") == 0


def test_empty_source():
    assert outline.count_top_level_functions("") == 0


def test_two_top_level_functions():
    assert outline.count_top_level_functions("def a():\n    pass\n\ndef b():\n    return 1\n") == 2


def test_async_function_counts():
    assert outline.count_top_level_functions("async def f():\n    pass\n") == 1


def test_nested_functions_and_methods_are_not_counted():
    source = "def outer():\n    def inner():\n        pass\n\nclass C:\n    def method(self):\n        pass\n"
    assert outline.count_top_level_functions(source) == 1


def test_syntax_error_does_not_raise_and_recognisable_functions_are_counted():
    assert outline.count_top_level_functions(SYNTAX_ERROR_SOURCE) == 2


def test_the_parser_library_named_in_the_goal_is_used():
    outline.count_top_level_functions("x = 1\n")
    assert "tree_sitter" in sys.modules
