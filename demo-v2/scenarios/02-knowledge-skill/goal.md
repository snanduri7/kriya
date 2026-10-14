Implement `function_start_offsets(data: bytes) -> list[int]` in the existing `outline.py`. `data` is Python source code encoded as UTF-16 little-endian without a byte-order mark. Return, in source order, the byte offset within `data` at which each function defined directly at the top level begins; an `async` function begins at its `async` keyword. Functions nested inside other functions, and methods inside classes, are not included.

Use py-tree-sitter (tree-sitter==0.25.2) with its Python grammar (tree-sitter-python==0.25.0), not the standard library `ast` module.

`test_outline.py` already contains the tests. Change only `outline.py`; do not create or modify any other file.
