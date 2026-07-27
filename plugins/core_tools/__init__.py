import os
import re
import ast
import fnmatch
import asyncio
from typing import Type, Optional, Any, List, Dict
from pydantic import BaseModel, Field

from kriya.plugins.plugin import BasePlugin
from kriya.tools.tool import BaseTool, ToolExecutionError

# =====================================================================
# 1. Tool Arguments Schemas
# =====================================================================

class FilesystemArgs(BaseModel):
    operation: str = Field(description="The file operation to perform: 'read', 'write', or 'list'")
    path: str = Field(description="The target file or directory path")
    content: Optional[str] = Field(default=None, description="The file content to write (required only for write operation)")
    start_line: Optional[int] = Field(default=None, description="The start line number for chunked read (1-indexed, inclusive)")
    end_line: Optional[int] = Field(default=None, description="The end line number for chunked read (1-indexed, inclusive)")

class ShellArgs(BaseModel):
    command: str = Field(description="The shell command to execute")

class GitArgs(BaseModel):
    subcommand: str = Field(description="The git subcommand: 'status', 'diff', 'log', 'commit', 'branch', or 'blame'")
    message: Optional[str] = Field(default=None, description="The commit message (required only for 'commit' subcommand)")
    file_path: Optional[str] = Field(default=None, description="The file path for blame operations")

class SearchArgs(BaseModel):
    pattern: str = Field(description="The regex pattern to search for in files")
    path: str = Field(default=".", description="The base directory to start searching in")
    file_glob: str = Field(default="*", description="Filter files matching glob pattern (e.g. '*.py')")

class ASTArgs(BaseModel):
    file_path: str = Field(description="The path to the Python or Java file to analyze")

# =====================================================================
# 2. Tool Implementations
# =====================================================================

class FilesystemTool(BaseTool):
    @property
    def name(self) -> str:
        return "filesystem"

    @property
    def description(self) -> str:
        return "Read, write, or list files on the local system, supporting chunked lines reading."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return FilesystemArgs

    async def _run(self, args: FilesystemArgs) -> Any:
        path = os.path.abspath(args.path)
        op = args.operation.lower()

        if op == "read":
            if not os.path.exists(path):
                raise ToolExecutionError(f"File '{path}' does not exist.")
            if not os.path.isfile(path):
                raise ToolExecutionError(f"Path '{path}' is not a file.")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    if args.start_line is not None or args.end_line is not None:
                        lines = f.readlines()
                        start = (args.start_line - 1) if args.start_line is not None else 0
                        end = args.end_line if args.end_line is not None else len(lines)
                        
                        # clamp boundaries
                        start = max(0, min(start, len(lines)))
                        end = max(0, min(end, len(lines)))
                        
                        return "".join(lines[start:end])
                    else:
                        return f.read()
            except Exception as e:
                raise ToolExecutionError(f"Failed to read file '{path}': {e}")

        elif op == "write":
            if args.content is None:
                raise ToolExecutionError("Content is required for 'write' operation.")
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(args.content)
                return f"Successfully wrote to file '{path}'"
            except Exception as e:
                raise ToolExecutionError(f"Failed to write to file '{path}': {e}")

        elif op == "list":
            if not os.path.exists(path):
                raise ToolExecutionError(f"Directory '{path}' does not exist.")
            if not os.path.isdir(path):
                raise ToolExecutionError(f"Path '{path}' is not a directory.")
            try:
                return os.listdir(path)
            except Exception as e:
                raise ToolExecutionError(f"Failed to list directory '{path}': {e}")
        else:
            raise ToolExecutionError(f"Unsupported filesystem operation '{args.operation}'")


class ShellTool(BaseTool):
    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return "Execute arbitrary shell commands on the local machine."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return ShellArgs

    async def _run(self, args: ShellArgs) -> Any:
        try:
            process = await asyncio.create_subprocess_shell(
                args.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            return {
                "exit_code": process.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace")
            }
        except Exception as e:
            raise ToolExecutionError(f"Shell command execution failed: {e}")


class GitTool(BaseTool):
    @property
    def name(self) -> str:
        return "git"

    @property
    def description(self) -> str:
        return "Perform basic Git operations like status, diff, log, commit, branch, and blame."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return GitArgs

    async def _run(self, args: GitArgs) -> Any:
        sub = args.subcommand.lower()
        cmd = ["git"]

        if sub == "status":
            cmd.append("status")
        elif sub == "diff":
            cmd.append("diff")
        elif sub == "log":
            cmd.extend(["log", "-n", "5", "--oneline"])
        elif sub == "branch":
            cmd.append("branch")
        elif sub == "commit":
            if not args.message:
                raise ToolExecutionError("Commit message is required for git commit.")
            cmd.extend(["commit", "-m", args.message])
        elif sub == "blame":
            if not args.file_path:
                raise ToolExecutionError("file_path is required for git blame.")
            cmd.extend(["blame", args.file_path])
        else:
            raise ToolExecutionError(f"Unsupported git subcommand: {args.subcommand}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                raise ToolExecutionError(
                    f"Git command failed with exit code {process.returncode}: {stderr.decode('utf-8')}"
                )
                
            return stdout.decode("utf-8")
        except Exception as e:
            if isinstance(e, ToolExecutionError):
                raise e
            raise ToolExecutionError(f"Git execution failed: {e}")


class SearchTool(BaseTool):
    @property
    def name(self) -> str:
        return "search"

    @property
    def description(self) -> str:
        return "Perform text or regex pattern search across workspace files."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return SearchArgs

    async def _run(self, args: SearchArgs) -> Any:
        base_dir = os.path.abspath(args.path)
        if not os.path.exists(base_dir) or not os.path.isdir(base_dir):
            raise ToolExecutionError(f"Search directory '{args.path}' does not exist.")

        try:
            regex = re.compile(args.pattern, re.IGNORECASE)
        except re.error as e:
            raise ToolExecutionError(f"Invalid search pattern regex: {e}")

        ignore_dirs = {
            ".git", ".venv", "venv", "node_modules", "__pycache__", 
            ".pytest_cache", "build", "dist", ".egg-info"
        }

        matches = []
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]

            for file in files:
                if not fnmatch.fnmatch(file, args.file_glob):
                    continue

                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                rel = os.path.relpath(file_path, base_dir)
                                matches.append(f"{rel}:{line_num}: {line.strip()}")
                except Exception:
                    pass

        if not matches:
            return "No matches found."
        return "\n".join(matches[:150]) # cap matches


class ASTTool(BaseTool):
    @property
    def name(self) -> str:
        return "ast"

    @property
    def description(self) -> str:
        return "Analyze file structures, classes, methods, Spring annotations and XML bean configs."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return ASTArgs

    async def _run(self, args: ASTArgs) -> Any:
        path = os.path.abspath(args.file_path)
        if not os.path.exists(path) or not os.path.isfile(path):
            raise ToolExecutionError(f"Target file '{args.file_path}' does not exist.")

        # 1. Parse Python
        if path.endswith(".py"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    code = f.read()
                parsed = ast.parse(code)
                classes = []
                functions = []
                for node in ast.iter_child_nodes(parsed):
                    if isinstance(node, ast.ClassDef):
                        methods = [c.name for c in node.body if isinstance(c, ast.FunctionDef)]
                        classes.append(f"Class: {node.name} (methods: {', '.join(methods)})")
                    elif isinstance(node, ast.FunctionDef):
                        functions.append(f"def {node.name}")
                
                output = []
                if classes:
                    output.append("=== Python Classes ===\n" + "\n".join(classes))
                if functions:
                    output.append("=== Python Functions ===\n" + "\n".join(functions))
                return "\n\n".join(output) if output else "No classes/functions found."
            except Exception as e:
                raise ToolExecutionError(f"Failed to parse Python AST: {e}")

        # 2. Parse Java / Spring Source
        elif path.endswith(".java"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Basic regex sweeps for structure
                pkg_match = re.search(r"package\s+([\w\.]+);", content)
                package_name = pkg_match.group(1) if pkg_match else "default"

                class_matches = re.findall(r"(class|interface)\s+(\w+)\s*(?:extends\s+\w+)?\s*(?:implements\s+[\w\s,]+)?\s*\{", content)
                methods = re.findall(r"(?:public|protected|private|static|\s)+\s+[\w<>]+\s+(\w+)\s*\([^\)]*\)\s*(?:throws\s+[\w\s,]+)?\s*\{", content)
                
                # Scan for Spring annotations
                spring_annots = re.findall(r"@(Component|Service|Repository|RestController|Controller|Autowired|Qualifier|Bean)\b", content)

                output = [
                    f"Java Package: {package_name}",
                    f"Declarations: {', '.join([f'{m[0]} {m[1]}' for m in class_matches])}",
                    f"Methods found: {', '.join(methods[:20])}"
                ]
                if spring_annots:
                    output.append(f"Spring Annotations: {', '.join(set(spring_annots))}")
                return "\n".join(output)
            except Exception as e:
                raise ToolExecutionError(f"Failed to parse Java file structure: {e}")

        # 3. Parse Spring XML Bean Config
        elif path.endswith(".xml"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # Check for spring bean identifiers
                beans = re.findall(r'<bean\s+[^>]*id=["\']([^"\']+)["\']', content)
                classes = re.findall(r'<bean\s+[^>]*class=["\']([^"\']+)["\']', content)
                
                output = ["=== XML Config File ==="]
                if beans:
                    output.append(f"Spring XML Bean definitions found: {', '.join(beans)}")
                if classes:
                    output.append(f"Mapped classes: {', '.join(classes)}")
                
                if len(output) == 1:
                    return "Standard XML file (no Spring bean definitions identified)."
                return "\n".join(output)
            except Exception as e:
                raise ToolExecutionError(f"Failed to scan XML configuration: {e}")

        else:
            return "File format not supported for static AST analysis."


# =====================================================================
# 3. Core Tools Plugin Declaration
# =====================================================================

class CoreToolsPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "core_tools"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def initialize(self) -> None:
        self.kernel.registry.register("tool", "filesystem", FilesystemTool())
        self.kernel.registry.register("tool", "shell", ShellTool())
        self.kernel.registry.register("tool", "git", GitTool())
        self.kernel.registry.register("tool", "search", SearchTool())
        self.kernel.registry.register("tool", "ast", ASTTool())

    async def shutdown(self) -> None:
        self.kernel.registry.unregister("tool", "filesystem")
        self.kernel.registry.unregister("tool", "shell")
        self.kernel.registry.unregister("tool", "git")
        self.kernel.registry.unregister("tool", "search")
        self.kernel.registry.unregister("tool", "ast")
