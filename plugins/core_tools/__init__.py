import ast
import fnmatch
import logging
import os
import re
import shlex
from typing import Any, Callable, Optional, Tuple, Type

from pydantic import BaseModel, Field

from kriya.config.config import AutonomyConfig, ExecutionPolicyConfig
from kriya.platform import git_argv
from kriya.plugins.plugin import BasePlugin
from kriya.policy.enforcement import enforce_hard_invariants
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy, classify_shell_acquisition_command
from kriya.policy.filesystem import (
    AuthorizedFileReader,
    AuthorizedFileWriter,
    WriteScopeMode,
    is_trusted_control_path,
    trusted_control_path_denial,
    trusted_control_path_read_denial,
)
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision
from kriya.repository.git import refuse_repository_content_filters
from kriya.tools.containment import (
    ContainmentProfile,
    ContainmentSetupError,
    NetworkAuthority,
    TrustClass,
    resolve_containment_backend,
)
from kriya.tools.process import ProcessController
from kriya.tools.tool import (
    TOOL_CANDIDATE_VIEW_UNAVAILABLE,
    TOOL_OPERATOR_ONLY,
    BaseTool,
    ModelInvocationRefused,
    ModelToolInvocation,
    ToolExecutionError,
    ToolGovernance,
)

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
# 2. Model-authored call authority (S1, OI-88)
# =====================================================================
# A planner-emitted TOOL subtask reaches these tools through kriya.tools.tool.execute_model_authored(), which calls each
# tool's authorize_model_invocation() first. Each one reuses the EXISTING authority for the equivalent agent-generated
# effect - never a second policy engine: AuthorizedFileReader (DEV-INV-001, the Developer's own model-requested reads),
# AuthorizedFileWriter (the model-directed write choke point), the PLAT-039 trusted-control-path denial. The operator's
# own `kriya tools execute` keeps calling BaseTool.execute(), untouched.

_ALLOWED = (PolicyDecision.ALLOW, PolicyDecision.ALLOW_SANDBOXED)


def _authorized_read(invocation: ModelToolInvocation, path: str, *, in_owner: bool = False) -> str:
    """The path a model-authored read may use: resolved exactly as the tool's effect resolves it (S18c, OI-147: against
    the invocation's read root - the candidate for a step that reads the candidate (S18d), else the owner workspace; or the
    owner workspace when ``in_owner``), then authorized by AuthorizedFileReader over the invocation's roots (canonical
    containment, symlinks resolved, credential stores) - the same reader whichever root the path landed in."""
    target = invocation.resolve(path, in_owner=in_owner)
    AuthorizedFileReader(invocation.roots[0], invocation.roots[1:]).raise_if_denied(target)
    if _is_control_path(invocation, target):
        raise PolicyDeniedError(request=ActionRequest(action_type=ActionType.READ_FILE, target=target),
                                result=trusted_control_path_read_denial(target))
    return target


def _is_control_path(invocation: ModelToolInvocation, target: str) -> bool:
    """S18d (OI-150 c): whether a model-authored read would report repository metadata or Kriya control state as source
    - the canonical PLAT-039 rule (kriya.policy.filesystem.is_trusted_control_path: .git, .kriya and the scoped-snapshot
    sentinel, at any depth, any spelling) under the innermost root holding the target: a run's candidate worktree lives
    under the owner's own .kriya, so judged from the owner every candidate file would look like control state. A target
    no root holds is judged under every root (fail closed). The plugin knows no snapshot layout."""
    real_target = os.path.realpath(target)
    holding = [root for root in invocation.roots
               if os.path.commonpath([os.path.realpath(root), real_target]) == os.path.realpath(root)]
    if not holding:
        return any(is_trusted_control_path(root, target) for root in invocation.roots)
    return is_trusted_control_path(max(holding, key=lambda root: len(os.path.realpath(root))), target)


# =====================================================================
# 3. Tool Implementations
# =====================================================================

class FilesystemTool(BaseTool):
    def mutates_workspace(self, arguments):
        return str(arguments.get("operation", "")).lower() not in ("read", "list")

    @property
    def name(self) -> str:
        return "filesystem"

    @property
    def planned_governance(self) -> ToolGovernance:
        """S18: planned reads/lists only; every planned write stays refused per call (AuthorizedFileWriter DENY_ALL, OI-128)."""
        return ToolGovernance.READ_ONLY

    @property
    def description(self) -> str:
        return "Read, write, or list files on the local system, supporting chunked lines reading."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return FilesystemArgs

    def authorize_model_invocation(self, args: FilesystemArgs, invocation: ModelToolInvocation) -> FilesystemArgs:
        """S1: a planned read/list is a model-requested read (AuthorizedFileReader). Every other operation is a write
        and is refused in every mode by AuthorizedFileWriter in DENY_ALL scope: outside the roots
        (PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS), into .git/.kriya under any spelling (TRUSTED_CONTROL_PATH_DENIED),
        otherwise WRITE_SCOPE_DENY_ALL - a planned tool write lands in the owner folder outside the candidate, its
        gates and its review (nothing reaches the owner workspace before apply). Whether a governed in-candidate
        TOOL write should exist is the owner question in the S1 report."""
        if args.operation.lower() in ("read", "list"):
            return args.model_copy(update={"path": _authorized_read(invocation, args.path)})
        target = invocation.resolve(args.path, in_owner=True)  # refused below whatever it names
        writer = AuthorizedFileWriter(invocation.roots[0], invocation.roots[1:], write_scope_mode=WriteScopeMode.DENY_ALL)
        raise PolicyDeniedError(request=ActionRequest(action_type=ActionType.WRITE_FILE, target=target),
                                result=writer.authorize(target))

    async def _run_model_authored(self, args: FilesystemArgs, invocation: ModelToolInvocation) -> Any:
        """S18d (OI-150 c): a planned listing leaves out the control entries (_is_control_path); a read reaches here
        only for a path _authorized_read admitted."""
        output = await self._run(args)
        if args.operation.lower() != "list":
            return output
        base = os.path.abspath(args.path)
        return [entry for entry in output if not _is_control_path(invocation, os.path.join(base, entry))]

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
    def planned_governance(self) -> ToolGovernance:
        """S18 (OI-127, Option B): a planned shell command runs in the owner folder and no backend proves its effects stay
        out of the run's evidence (S18-PREFLIGHT.md R01-R31, R80); only an operator may run it."""
        return ToolGovernance.OPERATOR_ONLY

    @property
    def description(self) -> str:
        return "Execute arbitrary shell commands on the local machine."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return ShellArgs

    @property
    def requires_confirmation(self) -> bool:
        return True

    def authorize_model_invocation(self, args: ShellArgs, invocation: ModelToolInvocation) -> ShellArgs:
        """S18 (OI-127, Option B): OPERATOR_ONLY - a planned call is refused in every mode. execute_model_authored's
        governance recheck answers first (TOOL_OPERATOR_ONLY; in human-in-the-loop the executor's B3 stop answers
        before that); this is the tool's own second wall for any caller that reaches it directly."""
        raise ModelInvocationRefused(TOOL_OPERATOR_ONLY, f"tool {self.name!r} is operator-only; a planned step never "
                                                         "runs it. Nothing was run.")

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
        #
        # SEC-005 O5 (2026-09-13): network is UNRESTRICTED unconditionally
        # UNLESS BOTH (a) `contained_execution_required=True` (the same
        # opt-in gate PolymorphicValidator's own acquisition path already
        # uses - "preserve current documented behavior when containment is
        # intentionally disabled", never redesigning that global decision)
        # AND (b) this exact invocation is recognized as package-manager-
        # acquisition-shaped (classify_shell_acquisition_command, reusing
        # SEC-006's own AutonomyConfig.acquisition_registry_hosts for the
        # two ecosystems it already authorizes - never a new, independently
        # maintained host list). A near-miss / unrecognized command is
        # UNCHANGED (still UNRESTRICTED) - this only ever NARROWS authority
        # for a positively-recognized acquisition shape, never widens
        # anything relative to today's behavior. An unmapped-but-recognized
        # package manager (npm/Bundler/RubyGems/Cargo/Gradle - no
        # registry-host authority declared for these today) fails CLOSED to
        # DENIED, never UNRESTRICTED.
        #
        # PRD-012: autonomy.shell_network "denied" (forced by the production
        # profile) removes the privileged default - anything that is not a
        # recognized, registry-scoped package-manager command gets no
        # network. With a non-network-capable backend that is a refusal
        # (BackendUnavailableError), never a host run with the network.
        shell_denied = self.autonomy_cfg.shell_network == "denied"
        network = NetworkAuthority.DENIED if shell_denied else NetworkAuthority.UNRESTRICTED
        network_destinations: Tuple[str, ...] = ()
        if self.autonomy_cfg.contained_execution_required:
            acquisition_kind = classify_shell_acquisition_command(parsed_command) if parsed_command else None
            if acquisition_kind == "registry_scoped":
                network = NetworkAuthority.DEPENDENCY_REGISTRY_ONLY
                network_destinations = tuple(sorted(set(self.autonomy_cfg.acquisition_registry_hosts)))
            elif acquisition_kind == "unmapped":
                network = NetworkAuthority.DENIED
        profile = None
        backend = None
        # Required containment is never skipped because sandbox_execution is
        # off: that combination used to run the command on the host.
        if (
            self.autonomy_cfg.sandbox_execution or self.autonomy_cfg.contained_execution_required
            or shell_denied
        ):
            profile = ContainmentProfile(
                trust_class=TrustClass.UNTRUSTED_EXECUTION,
                workspace_path=os.getcwd(),
                network=network,
                network_destinations=network_destinations,
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

        output = {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if result.egress is not None:
            output["egress"] = result.egress  # PRD-012: the network authority it ran under
        return output


class GitTool(BaseTool):
    # POL-001-P2: status/diff/log/branch(list)/blame are GIT_READ-shaped -
    # never gated as GIT_WRITE, matching ExecutionPolicy's own
    # default-allow-for-reads backstop (MA4.2). commit is the only
    # currently-supported mutating subcommand this tool exposes - no
    # push/reset/ref-delete capability exists here to gate.
    _MUTATING_SUBCOMMANDS = frozenset({"commit"})
    # S19 Q1: the subcommands whose git can run a content filter (they convert working-tree content).
    _FILTER_SUBCOMMANDS = frozenset({"status", "diff", "blame", "commit"})
    # S18b (OI-140, owner decision 3): which repository truth a PLANNED read describes. status and diff describe the
    # working tree the run is changing, so they read the run's candidate (the invocation's second root; B); log,
    # branch and blame describe the owner repository's history, refs and committed-line attribution, so they read the
    # owner workspace (the first root; A) - the candidate is a detached Kriya worktree that gains no commits during a
    # run. The operator's own `kriya tools execute` is unchanged (the process folder).
    _CANDIDATE_VIEW_SUBCOMMANDS = frozenset({"status", "diff"})
    # SEC-001-P6: this tool had no wall-clock timeout at all before its
    # ProcessController migration - every subcommand here is a fast,
    # finite, local git operation, so a generous-but-bounded ceiling
    # (matching ShellTool's own default) is a strict improvement with no
    # real-world behavior change for a healthy repo.
    _GIT_COMMAND_TIMEOUT_SECONDS = 60

    def __init__(self, execution_policy_cfg: Optional[ExecutionPolicyConfig] = None) -> None:
        self._execution_policy_cfg = execution_policy_cfg
        # A bare ExecutionPolicy() with no override, matching every other
        # real caller without config-derived sensitive-path patterns in
        # scope (see kriya/policy/execution.py's own comment on this
        # default) - same convention ShellTool's own POL-001 wiring uses.
        self._execution_policy = ExecutionPolicy()

    def mutates_workspace(self, arguments):
        return str(arguments.get("subcommand", "")).lower() in self._MUTATING_SUBCOMMANDS

    @property
    def name(self) -> str:
        return "git"

    @property
    def planned_governance(self) -> ToolGovernance:
        """S18: planned status/diff/log/branch/blame read; a planned commit stays refused per call (PLAT-039)."""
        return ToolGovernance.READ_ONLY

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

    def authorize_model_invocation(self, args: GitArgs, invocation: ModelToolInvocation) -> GitArgs:
        """S1: a planned commit writes repository metadata in the owner folder, outside Kriya's commit service:
        model-directed write authority never reaches .git (PLAT-039), in every mode and whatever the policy mode.
        blame's file is a model-requested read. S18c (OI-148): every planned read - status/diff/log/branch/blame, with
        two roots, one root, in place or none - then runs only where its root IS a repository's top level
        (_require_own_repository_view); path authorization answers first."""
        sub = args.subcommand.lower()
        if sub in self._MUTATING_SUBCOMMANDS:
            git_dir = os.path.join(invocation.roots[0], ".git") if invocation.roots else ".git"
            raise PolicyDeniedError(request=ActionRequest(action_type=ActionType.GIT_WRITE, command=("git", sub)),
                                    result=trusted_control_path_denial(git_dir))
        if sub == "blame" and args.file_path:
            # blame runs in the owner repository (S18b, A), so its file is that repository's, whatever the dependencies
            args = args.model_copy(update={"file_path": _authorized_read(invocation, args.file_path, in_owner=True)})
        root = self._planned_read_root(sub, invocation)
        # S18b: a SEPARATE candidate must also have a base revision, or its status/diff is not the run's change.
        separate = bool(invocation.roots) and os.path.realpath(root) != os.path.realpath(invocation.roots[0])
        self._require_own_repository_view(root, sub, base_revision=separate)
        return args

    @classmethod
    def _planned_read_root(cls, sub: str, invocation: ModelToolInvocation) -> str:
        """S18b (OI-140): the folder a planned read runs in. B (status/diff): the candidate - the second root when the
        run supplied one; with one root the owner folder IS what the run changes. A (log/branch/blame): the owner
        workspace. With no roots the process folder, as before."""
        if not invocation.roots:
            return os.getcwd()
        if sub in cls._CANDIDATE_VIEW_SUBCOMMANDS and len(invocation.roots) > 1:
            return invocation.roots[1]
        return invocation.roots[0]

    def _require_own_repository_view(self, root: str, sub: str, *, base_revision: bool) -> None:
        """S18c (OI-148): the explicit Kriya root is authoritative. git describes the repository it DISCOVERS from its
        working folder, walking upward, so a planned read in ``root`` describes ``root`` only when
        `git rev-parse --show-toplevel` is ``root`` itself (realpaths). A folder nested inside another repository (a
        workspace in a monorepo subfolder, or any subfolder of a repository) is not its own repository: git would
        answer for the ENCLOSING repository (MEASURED: its status listed ../outside.txt, its log the enclosing
        history), so the read is refused, typed, before git runs - never an upward walk, never a substitute root, no
        pathspec confinement. ``base_revision`` (S18b, a SEPARATE candidate's status/diff): it must also have a commit
        (`HEAD^{commit}`); the scoped snapshot of a nested workspace has none (empty diff, everything untracked)."""
        command = ["git", "rev-parse", "--show-toplevel", *(("HEAD^{commit}",) if base_revision else ())]
        profile = ContainmentProfile(trust_class=TrustClass.TRUSTED_KRIYA_INFRASTRUCTURE, workspace_path=root)
        try:
            probe = ProcessController().run(
                git_argv(command), cwd=root, timeout=self._GIT_COMMAND_TIMEOUT_SECONDS, containment_profile=profile,
                containment_backend=resolve_containment_backend("none"),
            )
            lines = probe.stdout.splitlines() if probe.returncode == 0 and not probe.timeout else []
        except Exception as error:  # an unreadable repository is no view at all
            logger.warning("GitTool: the repository view of %r could not be read (%s)", root, error)
            lines = []
        # one line per argument: the top level, then (with base_revision) the commit
        if len(lines) != (2 if base_revision else 1) or os.path.realpath(lines[0]) != os.path.realpath(root):
            what = ("a repository top level with a base revision of its own, so it cannot describe what the run "
                    "changed" if base_revision else "the top level of its own git repository, so git would describe "
                    "an enclosing repository (or none) instead")
            raise ModelInvocationRefused(
                TOOL_CANDIDATE_VIEW_UNAVAILABLE,
                f"a planned git {sub} needs the repository at {root!r} itself, and that folder is not {what}. "
                "Nothing was run.",
            )

    async def _run(self, args: GitArgs) -> Any:
        return await self._run_in(args, os.getcwd())

    async def _run_model_authored(self, args: GitArgs, invocation: ModelToolInvocation) -> Any:
        """S18 (OI-129 item 3) / S18b (OI-140): a planned git read runs in the root its subject belongs to
        (_planned_read_root): status/diff in the candidate, log/branch/blame in the owner workspace."""
        return await self._run_in(args, self._planned_read_root(args.subcommand.lower(), invocation))

    async def _run_in(self, args: GitArgs, cwd: str) -> Any:
        sub = args.subcommand.lower()
        cmd = ["git"]

        if sub == "status":
            cmd.append("status")
        elif sub == "diff":
            # S19 (OI-141, owner question Q3; the fail-closed choice): no external diff command and no textconv
            # driver - a repository's diff.<driver>.command / textconv would otherwise run on the host (MEASURED).
            cmd.extend(["diff", "--no-ext-diff", "--no-textconv"])
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
            cmd.extend(["blame", "--no-textconv", args.file_path])  # S19 (Q3): no repository textconv driver
        else:
            raise ToolExecutionError(f"Unsupported git subcommand: {args.subcommand}")

        if sub in self._MUTATING_SUBCOMMANDS:
            self._authorize_git_write(cmd)
        if sub in self._FILTER_SUBCOMMANDS:
            # S19 Q1 (OI-141): status/diff/blame/commit can run a content filter; a repository that defines one in its
            # own configuration is refused, typed, before git runs (log and branch convert no file content).
            refuse_repository_content_filters(cwd)

        # SEC-001-P6 (2026-09-11): migrated off a raw asyncio.create_subprocess_exec
        # call with no timeout and no process-group isolation onto the common
        # execution boundary - this tool's own execution-surface inventory
        # entry (deferred in the earlier foundation package) is now closed.
        # TrustClass.TRUSTED_KRIYA_INFRASTRUCTURE, not UNTRUSTED_EXECUTION:
        # `cmd` is always ONE of this method's own fixed-shape git
        # invocations (status/diff/log/branch/commit/blame) - `args.message`/
        # `args.file_path` land as inert argv DATA (a `-m <message>` value,
        # a path git reads), never as executed content, the same reasoning
        # worktree.py's own bootstrap commands already rely on for their
        # ALLOW classification (kriya/policy/execution.py's
        # _is_kriya_internal_bootstrap_commit). backend_required is False
        # for this trust class, so no containment backend is needed here -
        # this migration is purely about gaining a real timeout and
        # process-tree cleanup, which this tool had neither of before.
        profile = ContainmentProfile(
            trust_class=TrustClass.TRUSTED_KRIYA_INFRASTRUCTURE,
            workspace_path=cwd,
        )
        try:
            result = await ProcessController().run_async(
                git_argv(cmd), cwd=cwd, timeout=self._GIT_COMMAND_TIMEOUT_SECONDS,
                containment_profile=profile, containment_backend=resolve_containment_backend("none"),
            )
            if result.timeout:
                raise ToolExecutionError(
                    f"Git command timed out after {self._GIT_COMMAND_TIMEOUT_SECONDS} seconds: {' '.join(cmd)}"
                )
            if result.returncode != 0:
                raise ToolExecutionError(
                    f"Git command failed with exit code {result.returncode}: {result.stderr}"
                )
            return result.stdout
        except Exception as e:
            if isinstance(e, ToolExecutionError):
                raise e
            raise ToolExecutionError(f"Git execution failed: {e}") from e


class SearchTool(BaseTool):
    def mutates_workspace(self, arguments):
        return False

    @property
    def name(self) -> str:
        return "search"

    @property
    def planned_governance(self) -> ToolGovernance:
        return ToolGovernance.READ_ONLY

    @property
    def description(self) -> str:
        return "Perform text or regex pattern search across workspace files."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return SearchArgs

    def authorize_model_invocation(self, args: SearchArgs, invocation: ModelToolInvocation) -> SearchArgs:
        """S1: the searched directory is a model-requested read (AuthorizedFileReader); so is every file the walk
        opens (_run_model_authored)."""
        return args.model_copy(update={"path": _authorized_read(invocation, args.path)})

    async def _run_model_authored(self, args: SearchArgs, invocation: ModelToolInvocation) -> Any:
        reader = AuthorizedFileReader(invocation.roots[0], invocation.roots[1:])
        # S18d (OI-150 c): a control file (a worktree's .git gitfile, the snapshot sentinel) is never a search hit.
        return self._search(args, admit=lambda file_path: reader.authorize(file_path).decision in _ALLOWED
                            and not _is_control_path(invocation, file_path))

    async def _run(self, args: SearchArgs) -> Any:
        return self._search(args)

    def _search(self, args: SearchArgs, admit: Optional[Callable[[str], bool]] = None) -> Any:
        """``admit`` (model-authored calls): a file it rejects - e.g. a symlink resolving outside the roots or a
        credential store - is skipped unread."""
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
                if admit is not None and not admit(file_path):
                    continue
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
    def mutates_workspace(self, arguments):
        return False

    @property
    def name(self) -> str:
        return "ast"

    @property
    def planned_governance(self) -> ToolGovernance:
        return ToolGovernance.READ_ONLY

    @property
    def description(self) -> str:
        return "Analyze file structures, classes, methods, Spring annotations and XML bean configs."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return ASTArgs

    def authorize_model_invocation(self, args: ASTArgs, invocation: ModelToolInvocation) -> ASTArgs:
        """S1: the analyzed file is a model-requested read (AuthorizedFileReader)."""
        return args.model_copy(update={"file_path": _authorized_read(invocation, args.file_path)})

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
# 4. Core Tools Plugin Declaration
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
