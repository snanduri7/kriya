<!-- SCENARIO-B-COMPLEX-TASK-NOT-DEMO-SUITABLE (owner decision 2026-10-14). Kept as evidence of the run in handover/DEMO-V2/evidence/scenario-02-live-retry-1/; not used by the scenario. Not a Kriya defect. -->
Create `outline.py` with a function `function_names(source: str) -> list[str]` that returns the names of every function defined in the given Python source code, in source order, including nested functions, methods inside classes and async functions. It must not raise when the source contains a syntax error (return the names that can be found, or an empty list).

Use py-tree-sitter (tree-sitter==0.25.2) with its Python grammar (tree-sitter-python==0.25.0), not the standard library `ast` module.

Add `test_outline.py` with pytest tests for: no functions, one function, a nested function, methods in a class, an async function, and a source with a syntax error. Create both files together as one change, create only these two files, and do not modify any existing file.
