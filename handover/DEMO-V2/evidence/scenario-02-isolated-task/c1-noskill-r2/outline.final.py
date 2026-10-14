"""Counting functions in Python source code."""
import tree_sitter_python
from tree_sitter import Language, Parser


def count_top_level_functions(source: str) -> int:
    # Create a Python language object
    python_lang = Language(tree_sitter_python.language())
    
    # Initialize the parser with the language directly
    parser = Parser(python_lang)
    
    try:
        # Parse the source code
        tree = parser.parse(bytes(source, "utf8"))
    except Exception:
        # If parsing fails completely, return 0
        return 0

    # Count the top-level functions
    count = 0
    for node in tree.root_node.children:
        # Check if the node is a function definition at the top level
        # Include both regular and async function definitions
        if node.type in ["function_definition", "async_function_definition"]:
            # Verify this function is directly at the top level
            # If its parent is the root node, it is top-level
            if node.parent == tree.root_node:
                count += 1

    return count

