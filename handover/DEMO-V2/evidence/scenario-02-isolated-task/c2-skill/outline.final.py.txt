"""Locating functions in Python source code."""


def function_start_offsets(data: bytes) -> list[int]:
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser

    PY_LANGUAGE = Language(tspython.language())
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(data, encoding='utf16')
    
    offsets = []
    for node in tree.root_node.children:
        if node.type == 'function_definition':
            # Check if this function is at the top level (not inside another function or class)
            parent = node.parent
            while parent and parent.type != 'module':
                if parent.type in ('function_definition', 'class_definition'):
                    break
                parent = parent.parent
            else:
                # This is a top-level function
                offsets.append(node.start_byte)
    
    return sorted(offsets)
