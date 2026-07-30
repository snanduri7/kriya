import ast
import fnmatch
import logging
import os
import re

# Let's import MCPServer from the mcp SDK (formerly FastMCP, renamed in mcp>=2.0.0)
from mcp.server.mcpserver import MCPServer

# Instantiate MCP server
mcp = MCPServer("kriya")

logger = logging.getLogger(__name__)

# =====================================================================
# 1. AST Parser Tool
# =====================================================================

@mcp.tool()
def parse_ast(file_path: str) -> str:
    """Extracts classes, methods, and functions from a Python source file.
    
    Args:
        file_path: The absolute or relative path to the Python source file.
    """
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return f"Error: File '{file_path}' does not exist."
    if not os.path.isfile(abs_path):
        return f"Error: Path '{file_path}' is not a file."
    if not file_path.endswith(".py"):
        return "Error: AST parsing is only supported for Python (.py) files."

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            code = f.read()

        parsed = ast.parse(code)
        
        classes = []
        functions = []
        
        for node in ast.iter_child_nodes(parsed):
            if isinstance(node, ast.ClassDef):
                methods = []
                for child in node.body:
                    if isinstance(child, ast.FunctionDef):
                        arg_names = [arg.arg for arg in child.args.args]
                        methods.append(f"  - {child.name}({', '.join(arg_names)})")
                
                base_names = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        base_names.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        base_names.append(f"{base.value.id}.{base.attr}")
                        
                base_str = f"({', '.join(base_names)})" if base_names else ""
                class_desc = f"Class {node.name}{base_str}:"
                if methods:
                    class_desc += "\n" + "\n".join(methods)
                else:
                    class_desc += "\n  (No methods)"
                classes.append(class_desc)
                
            elif isinstance(node, ast.FunctionDef):
                arg_names = [arg.arg for arg in node.args.args]
                functions.append(f"def {node.name}({', '.join(arg_names)})")

        output = []
        if classes:
            output.append("=== Classes ===")
            output.append("\n\n".join(classes))
        if functions:
            if output:
                output.append("")
            output.append("=== Top-level Functions ===")
            output.append("\n".join(functions))

        if not output:
            return "No class or function declarations found in the file."
            
        return "\n".join(output)

    except SyntaxError as se:
        return f"Syntax Error parsing file '{file_path}': line {se.lineno}, col {se.offset} - {se.text.strip() if se.text else ''}"
    except Exception as e:
        return f"Error parsing file '{file_path}': {e}"


# =====================================================================
# 2. Workspace Search Tool
# =====================================================================

@mcp.tool()
def search_code(pattern: str, path: str = ".", file_glob: str = "*") -> str:
    """Searches files in a directory path for a pattern, returning line matches.
    
    Args:
        pattern: The query pattern to search for (substring or regex).
        path: The directory path to search within (defaults to current directory).
        file_glob: Glob filter pattern for file basenames (e.g. '*.py' or '*').
    """
    abs_dir = os.path.abspath(path)
    if not os.path.exists(abs_dir):
        return f"Error: Directory '{path}' does not exist."
    if not os.path.isdir(abs_dir):
        return f"Error: Path '{path}' is not a directory."

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as re_err:
        return f"Error: Invalid search pattern regex: {re_err}"

    ignore_dirs = {
        ".git", ".venv", "venv", "node_modules", "__pycache__", 
        ".pytest_cache", "build", "dist", ".egg-info"
    }

    matches = []
    total_searched = 0

    for root, dirs, files in os.walk(abs_dir):
        # Modify dirs in-place to skip ignored directories
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]

        for file in files:
            if not fnmatch.fnmatch(file, file_glob):
                continue

            file_path = os.path.join(root, file)
            total_searched += 1
            
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            rel_path = os.path.relpath(file_path, abs_dir)
                            matches.append(f"{rel_path}:{line_num}: {line.strip()}")
            except Exception as e:
                logger.debug(f"Skipped unreadable file '{file_path}' during search: {e}")

    if not matches:
        return f"No matches found for '{pattern}' (searched {total_searched} files)."

    output = [
        f"=== Search results for '{pattern}' (found {len(matches)} matches in {total_searched} files) ===",
        ""
    ]
    output.extend(matches[:100]) # cap results at 100
    if len(matches) > 100:
        output.append(f"\n... and {len(matches) - 100} more matches truncated.")
        
    return "\n".join(output)


# =====================================================================
# 3. Main Executable Entrypoint
# =====================================================================

def main() -> None:
    """CLI script entrypoint to run the MCP tool server."""
    mcp.run()

if __name__ == "__main__":
    main()
