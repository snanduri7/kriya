Implement `count_top_level_functions(source: str) -> int` in the existing `outline.py`. It returns how many functions, including `async` functions, are defined directly at the top level of the given Python source code. Functions nested inside other functions, and methods inside classes, are not counted. It must not raise when the source contains a syntax error: it returns the number of top-level functions it can still recognise in the rest of the source.

Use py-tree-sitter (tree-sitter==0.25.2) with its Python grammar (tree-sitter-python==0.25.0), not the standard library `ast` module.

`test_outline.py` already contains the tests. Change only `outline.py`; do not create or modify any other file.
