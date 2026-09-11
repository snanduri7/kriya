import ast
import asyncio
import fnmatch
import logging
import os
import re
import shlex
from typing import Any, Optional, Type

from pydantic import BaseModel, Field

from kriya.config.config import AutonomyConfig, ExecutionPolicyConfig
from kriya.plugins.plugin import BasePlugin
from kriya.policy.enforcement import enforce_hard_invariants
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision
from kriya.tools.containment import (
    ContainmentProfile,
    ContainmentSetupError,
    NetworkAuthority,
    TrustClass,
    resolve_containment_backend,
)
from kriya.tools.process import ProcessController
from kriya.tools.tool import BaseTool, ToolExecutionError

logger = logging.getLogger(__name__)

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
                raise ToolExecutionError(f"Failed to read file '{path}': {e}") from e

        elif op == "write":
            if args.content is None:
                raise ToolExecutionError("Content is required for 'write' operation.")
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(args.content)
                return f"Successfully wrote to file '{path}'"
            except Exception as e:
                raise ToolExecutionError(f"Failed to write to file '{path}': {e}") from e

        elif op == "list":
            if not os.path.exists(path):
                raise ToolExecutionError(f"Directory '{path}' does not exist.")
            if not os.path.isdir(path):
                raise ToolExecutionError(f"Path '{path}' is not a directory.")
            try:
                return os.listdir(path)
            except Exception as e:
                raise ToolExecutionError(f"Failed to list directory '{path}': {e}") from e
        else:
            raise ToolExecutionError(f"Unsupported filesystem operation '{args.operation}'")


class ShellTool(BaseTool):
    def __init__(
        self, autonomy_cfg: Optional[AutonomyConfig] = None,
        execution_policy_cfg: Optional[ExecutionPolicyConfig] = None,
    ) -> None:
        self.autonomy_cfg = autonomy_cfg or AutonomyConfig()
        self._execution_policy_cfg = execution_policy_cfg
        # POL-001: this tool previously executed args.command with zero
        # ExecutionPolicy consultation - unlike kriya/tools/validate.py's
        # own Kriya-constructed compile/test commands, this string is
        # caller/model-supplied and can contain `sudo`, a force-push, a
        # protected-ref mutation, etc. Reuses the same always-on, 5-reason-
        # code hard stop kriya/tools/validate.py::_audit_run_command already
        # applies (kriya/policy/enforcement.py::enforce_hard_invariants) -
        # not a new authority, the same one, applied to a call site that
        # was missing it. A bare `ExecutionPolicy()` with no override
        # matches every other real caller that doesn't have config access
        # in scope (see execution.py's own comment on this default).
        self._execution_policy = ExecutionPolicy()

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return "Execute arbitrary shell commands on the local machine."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return ShellArgs

    @property
    def requires_confirmation(self) -> bool:
        return True

    async def _run(self, args: ShellArgs) -> Any:
        # POL-001: parsed best-effort the same way a real argv-based caller
        # would be; an unparseable string (mismatched quoting) is passed
        # through as a single opaque token rather than blocking execution -
        # this stage only ever adds a hard stop for a small, precise set of
        # reason codes (sudo/force-push/protected-ref/config-mutate/remote-
        # mutate), it never grants permission, so failing to parse just
        # means this particular check has no opinion, matching
        # _authorize_action's own "a broken check never blocks the caller"
        # precedent - it does not weaken any other enforcement.
        #
        # Mirrors kriya/tools/validate.py::_audit_run_command's own
        # try/except shape exactly: PolicyDeniedError propagates (a real
        # denial must actually stop execution), any OTHER exception from a
        # broken/misconfigured policy engine (e.g. a bad regex in a user's
        # autonomy.sensitive_paths) is logged and swallowed rather than
        # newly breaking a shell command that worked before this check
        # existed.
        try:
            parsed_command = tuple(shlex.split(args.command))
        except ValueError:
            parsed_command = (args.command,)
        if parsed_command:
            request = ActionRequest(
                action_type=ActionType.RUN_COMMAND,
                command=parsed_command,
                workspace_path=os.getcwd(),
            )
            # Unconditional (mode-independent) hard-invariant check - unchanged
            # from P1, must keep blocking sudo etc. even under mode="audit".
            try:
                enforce_hard_invariants(self._execution_policy, request)
            except PolicyDeniedError:
                raise
            except Exception as e:
                logger.debug("POL-001 policy check failed (ignored, fails open on a broken check only): %s", e)

            # POL-001-P2: enforce_hard_invariants only ever escalates 5
            # specific hard-DENY codes - it deliberately never acts on
            # REQUIRE_APPROVAL (kriya/policy/enforcement.py's own
            # docstring). A raw shell-wrapper invocation (`bash -c "..."`,
            # `sh -c "..."`, etc.) typed through this tool genuinely reaches
            # `_check_command_allowlist`'s COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL
            # rule, which the check above silently let through - found via
            # this task's own Step 4 re-audit, not by the P1 report. No
            # approval_callback is reachable at this plugin-tool boundary
            # (same as GitTool's own commit gate), so this mirrors that
            # exact mode-gated fail-closed pattern rather than inventing new
            # plumbing: audit-only under mode="audit" (today's default,
            # preserves existing behavior), fails closed under mode="enforce".
            #
            # Deliberately scoped to REQUIRE_APPROVAL only, NOT every
            # non-ALLOW decision - COMMAND_NOT_ALLOWLISTED DENY stays
            # excluded here exactly as it is in enforce_hard_invariants
            # itself (narrow starter allowlist, real risk of blocking
            # legitimate commands never on that list yet). Broadening that
            # is explicitly out of scope for this pass.
            enforce = bool(self._execution_policy_cfg and self._execution_policy_cfg.mode == "enforce")
            if enforce:
                try:
                    result = self._execution_policy.evaluate(request)
                except Exception as e:
                    logger.debug("POL-001 policy evaluation failed (ignored, audit-only): %s", e)
                    result = None
                if result is not None and result.decision == PolicyDecision.REQUIRE_APPROVAL:
                    raise PolicyDeniedError(request=request, result=result)

        # SEC-001 (2026-09-11): migrated off a raw asyncio.create_subprocess_shell
        # call with no timeout and no process-group isolation (the single
        # most dangerous, least-contained primitive found by the SEC-001
        # execution-surface inventory - see
        # docs/architecture/SEC001_HOSTILE_CODE_CONTAINMENT_DESIGN.md §1)
        # onto ProcessController.run_async(), the same common execution
        # boundary PolymorphicValidator/service_runtime already use.
        # `["/bin/sh", "-c", args.command]` reproduces
        # asyncio.create_subprocess_shell's own exact POSIX invocation
        # shape, so shell-metacharacter/pipe/redirect semantics are
        # unchanged - only the execution PRIMITIVE moved, not what the
        # command string is allowed to contain.
        #
        # A ContainmentProfile is always constructed (this tool's content
        # is caller/model-supplied, never Kriya-authored - TrustClass is
        # UNTRUSTED_EXECUTION, never inferred from the command text
        # itself), but the packaged default containment_backend ("none" -
        # NullContainmentBackend) reproduces sandbox_execution's exact
        # prior env-allowlist/rlimit-only behavior, so nothing about
        # ordinary shell-command results changes until a real backend is
        # configured. Backend/resource setup failure now genuinely blocks
        # the command (ContainmentSetupError propagates below) rather than
        # being silently caught by the generic ToolExecutionError wrap -
        # a caller needs to be able to tell "the shell command itself
        # failed" from "Kriya refused to run it uncontained".
        profile = None
        backend = None
        if self.autonomy_cfg.sandbox_execution:
            profile = ContainmentProfile(
                trust_class=TrustClass.UNTRUSTED_EXECUTION,
                workspace_path=os.getcwd(),
                network=NetworkAuthority.UNRESTRICTED,  # unchanged from today - no network gate existed here before either
                env_allowlist=self.autonomy_cfg.sandbox_env_allowlist,
                cpu_seconds=self.autonomy_cfg.sandbox_cpu_seconds,
                memory_mb=self.autonomy_cfg.sandbox_memory_mb,
            )
            backend = resolve_containment_backend(self.autonomy_cfg.containment_backend)
        controller = ProcessController()
        try:
            result = await controller.run_async(
                ["/bin/sh", "-c", args.command],
                cwd=os.getcwd(),
                timeout=self.autonomy_cfg.shell_command_timeout_seconds,
                containment_profile=profile,
                containment_backend=backend,
            )
        except ContainmentSetupError:
            raise
        except Exception as e:
            raise ToolExecutionError(f"Shell command execution failed: {e}") from e

        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }


class GitTool(BaseTool):
    # POL-001-P2: status/diff/log/branch(list)/blame are GIT_READ-shaped -
    # never gated as GIT_WRITE, matching ExecutionPolicy's own
    # default-allow-for-reads backstop (MA4.2). commit is the only
    # currently-supported mutating subcommand this tool exposes - no
    # push/reset/ref-delete capability exists here to gate.
    _MUTATING_SUBCOMMANDS = frozenset({"commit"})

    def __init__(self, execution_policy_cfg: Optional[ExecutionPolicyConfig] = None) -> None:
        self._execution_policy_cfg = execution_policy_cfg
        # A bare ExecutionPolicy() with no override, matching every other
        # real caller without config-derived sensitive-path patterns in
        # scope (see kriya/policy/execution.py's own comment on this
        # default) - same convention ShellTool's own POL-001 wiring uses.
        self._execution_policy = ExecutionPolicy()

    @property
    def name(self) -> str:
        return "git"

    @property
    def description(self) -> str:
        return "Perform basic Git operations like status, diff, log, commit, branch, and blame."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return GitArgs

    def _authorize_git_write(self, cmd: list) -> None:
        """POL-001-P2: `_check_git_destructive`'s own catch-all rule
        (kriya/policy/execution.py) returns REQUIRE_APPROVAL, never a bare
        ALLOW, for an ordinary `git commit` - none of the 5
        enforce_hard_invariants hard-DENY codes apply to a plain commit
        (those are force-push/protected-ref/config/remote-mutation
        specific), so P1's enforce_hard_invariants-only wiring would have
        left this REQUIRE_APPROVAL completely unconsulted. Mirrors
        WorkflowEngine._authorize_action's own established, already-tested
        semantics exactly (same module cannot be called directly - this is
        a plugin tool, not a WorkflowEngine method - so the same decision
        shape is replicated inline rather than inventing a different one):
        under mode="audit" (today's default), evaluate and let the caller
        decide/log, but never raise - audit-only, preserves today's actual
        behavior byte for byte. Under mode="enforce", DENY raises
        immediately; REQUIRE_APPROVAL has no approval_callback reachable at
        this boundary (a plugin tool, unlike run_generation_workflow, is
        never handed one) - Invariant 5 forbids adding callback plumbing
        for this, so it fails closed instead, exactly as this task's own
        Step 2 instructs ("otherwise document the current semantic and
        fail closed rather than inventing new architecture")."""
        request = ActionRequest(action_type=ActionType.GIT_WRITE, command=tuple(cmd), workspace_path=os.getcwd())
        try:
            result = self._execution_policy.evaluate(request)
        except Exception as e:
            logger.debug("POL-001 policy evaluation failed (ignored, audit-only): %s", e)
            return

        enforce = bool(self._execution_policy_cfg and self._execution_policy_cfg.mode == "enforce")
        if not enforce:
            return

        if result.decision in (PolicyDecision.ALLOW, PolicyDecision.ALLOW_SANDBOXED):
            return

        # DENY, or REQUIRE_APPROVAL with no reachable approval path: fail closed.
        raise PolicyDeniedError(request=request, result=result)

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
            # SEC-001-P1 (2026-09-11): suppresses any repository-defined
            # pre-commit/commit-msg/post-commit hook - this call can commit
            # into a real, possibly-adversarial target repository, and
            # nothing about this tool's job is "also run whatever hook that
            # repository happens to define".
            cmd.extend(["-c", "core.hooksPath=/dev/null", "commit", "-m", args.message])
        elif sub == "blame":
            if not args.file_path:
                raise ToolExecutionError("file_path is required for git blame.")
            cmd.extend(["blame", args.file_path])
        else:
            raise ToolExecutionError(f"Unsupported git subcommand: {args.subcommand}")

        if sub in self._MUTATING_SUBCOMMANDS:
            self._authorize_git_write(cmd)

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
            raise ToolExecutionError(f"Git execution failed: {e}") from e


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
            raise ToolExecutionError(f"Invalid search pattern regex: {e}") from e

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
                except Exception as e:
                    logger.debug(f"Skipped unreadable file '{file_path}' during search: {e}")

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
                raise ToolExecutionError(f"Failed to parse Python AST: {e}") from e

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
                raise ToolExecutionError(f"Failed to parse Java file structure: {e}") from e

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
                raise ToolExecutionError(f"Failed to scan XML configuration: {e}") from e

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
        from .validation_tool import ValidationTool
        autonomy_cfg = getattr(self.kernel.config, "autonomy", None) if self.kernel.config else None
        execution_policy_cfg = getattr(self.kernel.config, "execution_policy", None) if self.kernel.config else None
        self.kernel.registry.register("tool", "filesystem", FilesystemTool())
        self.kernel.registry.register(
            "tool", "shell", ShellTool(autonomy_cfg=autonomy_cfg, execution_policy_cfg=execution_policy_cfg),
        )
        self.kernel.registry.register("tool", "git", GitTool(execution_policy_cfg=execution_policy_cfg))
        self.kernel.registry.register("tool", "search", SearchTool())
        self.kernel.registry.register("tool", "ast", ASTTool())
        self.kernel.registry.register("tool", "validate_refactor", ValidationTool())

    async def shutdown(self) -> None:
        self.kernel.registry.unregister("tool", "filesystem")
        self.kernel.registry.unregister("tool", "shell")
        self.kernel.registry.unregister("tool", "git")
        self.kernel.registry.unregister("tool", "search")
        self.kernel.registry.unregister("tool", "ast")
        self.kernel.registry.unregister("tool", "validate_refactor")
