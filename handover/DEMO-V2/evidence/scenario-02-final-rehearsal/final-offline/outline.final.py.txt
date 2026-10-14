"""Locating functions in Python source code."""
import tree_sitter_python as tspython
from tree_sitter import Language, Parser


PY_LANGUAGE = Language(tspython.language())
parser = Parser(PY_LANGUAGE)


def function_start_offsets(data: bytes) -> list[int]:
    # Decode UTF-16 LE bytes to string
    source_str = data.decode('utf-16-le')
    
    # Encode to UTF-8 for tree-sitter parsing
    utf8_bytes = source_str.encode('utf-8')
    
    # Parse with tree-sitter
    tree = parser.parse(utf8_bytes)
    root_node = tree.root_node
    
    offsets = []
    
    for node in root_node.children:
        if node.type == 'function_definition':
            # Get the start byte of the function definition in UTF-8 encoded bytes
            start_byte_utf8 = node.start_byte
            
            # Convert UTF-8 byte offset to character index in original string
            # Find the character index such that len(source_str[:i].encode('utf-8')) == start_byte_utf8
            char_index = 0
            utf8_pos = 0
            for i, char in enumerate(source_str):
                char_bytes = char.encode('utf-8')
                if utf8_pos + len(char_bytes) > start_byte_utf8:
                    # This is the character that contains our byte offset
                    char_index = i
                    break
                utf8_pos += len(char_bytes)
            else:
                # If we didn't break, it means the offset is at the end
                char_index = len(source_str)
            
            # Now convert character index to UTF-16 LE byte offset
            utf16_offset = len(source_str[:char_index].encode('utf-16-le'))
            
            # For async functions, we need to check if the first child is 'async'
            # and use its start byte instead
            first_child = node.children[0] if node.children else None
            if first_child and first_child.type == 'async':
                # Get the start byte of the 'async' keyword in UTF-8
                async_start_utf8 = first_child.start_byte
                
                # Convert UTF-8 byte offset to character index in original string
                char_index_async = 0
                utf8_pos_async = 0
                for i, char in enumerate(source_str):
                    char_bytes = char.encode('utf-8')
                    if utf8_pos_async + len(char_bytes) > async_start_utf8:
                        # This is the character that contains our byte offset
                        char_index_async = i
                        break
                    utf8_pos_async += len(char_bytes)
                else:
                    # If we didn't break, it means the offset is at the end
                    char_index_async = len(source_str)
                
                # Convert to UTF-16 LE byte offset
                utf16_offset = len(source_str[:char_index_async].encode('utf-16-le'))
            
            offsets.append(utf16_offset)
    
    return offsets

