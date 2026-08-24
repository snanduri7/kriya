"""ExecutionPolicy - the deterministic decision engine itself. MA4.2 of the
control-plane implementation plan (see kriya/policy/__init__.py for MA4's
overall principle; kriya/policy/model.py for the MA4.1 domain types this
engine consumes and returns).

MA4.2 stood up the fixed evaluate() pipeline and its stage ORDER, with real
logic only in the platform-invariants stage (1) and the terminal
default-policy stage (8); stages 2-7 were deliberately stubbed (each
returning None - no rule matched, fall through), one per later MA4 sub-task,
so each lands as a pure addition to an already-tested pipeline rather than a
rewrite of it. MA4.4 filled in stage 6 (command allowlist, RUN_COMMAND
only). MA4.5 filled in stage 2 (filesystem, READ_FILE/WRITE_FILE only -
sensitive-path denial always, workspace-containment ALLOW/DENY when a
workspace_path is supplied). MA4.6 filled in stage 4 (network/egress,
NETWORK_ACCESS/LLM_NETWORK_ACCESS only - local/private target ALLOW, every
non-local target an explicit, specific DENY since no public-lookup
allowlist config exists yet). Stages 3, 5, 7 (git-destructive,
package/supply-chain, risk/profile approval) remain stubs, owned by MA4.7
through MA4.9 respectively.

MA4.4 gave ExecutionPolicy its first real caller (kriya/tools/validate.py's
PolymorphicValidator, before every ProcessController.run()); MA4.5 added a
second (kriya/workflow/edit_safety.py's atomic_write_file); MA4.6 added a
third (kriya/tools/web.py's fetch_url_text) - all audit-only: logged, never
gating, exactly like kriya/core/llm.py's MA4.3 integration.
This module itself still takes no config (constructor takes no arguments;
config wiring is MA4.15's job, added additively, not as a signature change -
the same "additive, not a signature change" precedent kriya/workflow/
process_profile.py's own docstring already established for MA2), and still
consults no LLM to reach a decision.
"""

import os
import re
from typing import Optional, Tuple

from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult

# MA4.4 - deliberately small starter allowlist (design doc section 18: "start
# small... do not try to support every shell command in MA4"). Matched by
# PREFIX (executable basename + a fixed run of leading subcommand tokens),
# not full literal argv - a real invocation carries extra flags (e.g.
# "mvn clean compile -Dmaven.compiler.showWarnings=true") that a bare-literal
# match would reject even though it's the same recognized operation. This is
# intentionally narrower than everything Kriya's own toolchain already runs
# internally (mvn dependency:build-classpath, javap, pip install, bundle
# install, rspec, python -m venv, ...) - those fall through to
# COMMAND_NOT_ALLOWLISTED below and are meant to, for now: this is AUDIT
# mode (see kriya/tools/validate.py's own MA4.4 integration), so the
# resulting "would have denied Kriya's own internal toolchain calls" signal
# is exactly the real-run comparison data the MA4 design doc's rollout
# section calls for before any future task grows this list or turns
# enforcement on - not a bug to silence by pre-approving everything now.
_ALLOWLISTED_COMMAND_PREFIXES: Tuple[Tuple[str, ...], ...] = (
    ("mvn", "compile"),
    ("mvn", "clean", "compile"),
    ("mvn", "test"),
    ("mvn", "clean", "test"),
    ("mvn", "verify"),
    ("mvn", "clean", "verify"),
    ("gradle", "test"),
    ("gradlew", "test"),
    ("pytest",),
    ("python", "-m", "pytest"),
    ("npm", "test"),
    ("npm", "run", "build"),
    ("go", "test"),
    ("cargo", "test"),
)

# Section 19: allowlisting a shell wrapper effectively allowlists arbitrary
# nested commands, so these never fall into the plain allowlist match above -
# they get their own, more cautious REQUIRE_APPROVAL rule instead.
_SHELL_WRAPPER_EXECUTABLES = frozenset({"sh", "bash", "zsh", "ksh", "dash"})


def _command_matches_prefix(command: Tuple[str, ...], prefix: Tuple[str, ...]) -> bool:
    if len(command) < len(prefix):
        return False
    if os.path.basename(command[0]) != os.path.basename(prefix[0]):
        return False
    return tuple(command[1:len(prefix)]) == tuple(prefix[1:])

# Section 11 of the MA4 design doc: this fixed order is itself a safety
# property. Placing filesystem/git/network/package restrictions ahead of the
# command allowlist and approval rules means a later stage's convenience
# match (e.g. "this looks like an allowlisted build command") can never
# override an earlier stage's hard restriction (e.g. "this touches a
# sensitive path") - the first matching stage wins, full stop.
_STAGE_METHOD_NAMES: Tuple[str, ...] = (
    "_check_platform_invariants",
    "_check_filesystem",
    "_check_git_destructive",
    "_check_network_egress",
    "_check_package_supply_chain",
    "_check_command_allowlist",
    "_check_approval_rules",
)

# Which ActionTypes are inherently read-only / non-consequential and may
# default-ALLOW when no earlier stage produced a decision. Deliberately a
# short, explicit allowlist rather than "everything not obviously
# destructive" - read_file and git_read are the only two MA4.1 action types
# that can never mutate anything Kriya doesn't already treat as safe to
# read, so they are the only two the engine may default-allow before their
# owning stage (MA4.5 for filesystem, MA4.8 for git) exists to add real
# path/ref-scoped rules on top.
_DEFAULT_ALLOW_ACTION_TYPES = frozenset({ActionType.READ_FILE, ActionType.GIT_READ})

# Per-ActionType minimum shape a well-formed ActionRequest must have -
# platform invariant #1 (section 11's stage 1). This is deliberately about
# PRESENCE, not value: whether "rm -rf /" is a safe command is stage 6's
# (MA4.4) job, not this one's. A request that doesn't even carry the field
# its own action_type requires is malformed on its face and fails closed
# regardless of what any later stage would have decided.
# MA4.5 - universal, context-free sensitive-path denials: these fire
# regardless of whether an ActionRequest carries a workspace_path, since
# "this is a credential/SSH-key/secrets path" doesn't depend on which repo
# is in play. Deliberately a small, hardcoded list here, NOT a read of
# kriya.config.config.AutonomyConfig.sensitive_paths - ExecutionPolicy still
# takes no config dependency at all (MA4.2's own principle; config wiring is
# MA4.15's job). This duplicates a few of that config's baseline patterns
# for now; MA4.15 is expected to replace this hardcoded list with a real
# config read rather than leaving two copies to drift.
_SENSITIVE_PATH_PATTERNS: Tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"(^|/)\.ssh(/|$)",
    r"(^|/)\.aws(/|$)",
    r"(^|/)\.kube(/|$)",
    r"(^|/)\.gnupg(/|$)",
    r"\.env$",
    r"credentials",
    r"secrets",
    r"password",
))


def _is_sensitive_path(normalized_path: str) -> bool:
    return any(p.search(normalized_path) for p in _SENSITIVE_PATH_PATTERNS)


def _normalize_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


_REQUIRED_FIELDS_BY_ACTION_TYPE = {
    ActionType.READ_FILE: ("target",),
    ActionType.WRITE_FILE: ("target",),
    ActionType.RUN_COMMAND: ("command",),
    ActionType.NETWORK_ACCESS: ("network_target",),
    ActionType.LLM_NETWORK_ACCESS: ("network_target",),
    ActionType.INSTALL_PACKAGE: ("target",),
    ActionType.GIT_READ: ("command",),
    ActionType.GIT_WRITE: ("command",),
    ActionType.PUBLISH_ARTIFACT: ("target",),
}


class ExecutionPolicy:
    """Policy decides whether an action is allowed; it never performs or
    enforces the action itself (see kriya/policy/__init__.py). evaluate()
    is the single entry point and is deterministic - no LLM is ever
    consulted to decide whether an action is allowed."""

    def evaluate(self, request: ActionRequest) -> PolicyResult:
        for stage_name in _STAGE_METHOD_NAMES:
            stage = getattr(self, stage_name)
            result = stage(request)
            if result is not None:
                return result
        return self._default_policy(request)

    def _check_platform_invariants(self, request: ActionRequest) -> Optional[PolicyResult]:
        required_fields = _REQUIRED_FIELDS_BY_ACTION_TYPE.get(request.action_type, ())
        missing = [f for f in required_fields if not getattr(request, f)]
        if missing:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="MALFORMED_ACTION_REQUEST",
                explanation=(
                    f"ActionRequest for '{request.action_type.value}' is missing "
                    f"required field(s): {', '.join(missing)}. Failing closed rather "
                    "than guessing intent."
                ),
                matched_rule="platform_invariants.required_fields",
            )
        return None

    def _check_filesystem(self, request: ActionRequest) -> Optional[PolicyResult]:
        """MA4.5 - governs READ_FILE and WRITE_FILE only. Two independent
        rules, per section 20:

        1. A small set of universally sensitive paths (~/.ssh, ~/.aws,
           ~/.kube, ~/.gnupg, .env, anything with credentials/secrets/
           password in it) always denies, with or without workspace
           context - reading or writing a credential file is never
           legitimate regardless of which repo the request claims to be
           for.
        2. Workspace containment: when the request carries a
           workspace_path (the real repo root or an active worktree root -
           this stage doesn't need to know which), a target inside it is
           explicitly ALLOWed (this already covers .kriya/ and any
           worktree under it, since both are subpaths of whatever root the
           caller passes) and a target outside it is explicitly DENYed.

        Without a workspace_path, rule 2 cannot run (there is nothing to
        check containment against) - this stage returns None for anything
        that isn't a sensitive-path hit, falling through to the stage 8
        default backstop (READ_FILE default-allows there; WRITE_FILE
        default-denies). See kriya/workflow/edit_safety.py's own MA4.5
        integration note for why its real call site can't supply
        workspace_path today - a known, deliberately-flagged limitation of
        this task's audit signal, not a silent gap."""
        if request.action_type not in (ActionType.READ_FILE, ActionType.WRITE_FILE):
            return None
        if not request.target:
            return None

        normalized = _normalize_path(request.target)
        if _is_sensitive_path(normalized):
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="SENSITIVE_PATH_DENIED",
                explanation=f"'{request.target}' matches a universally sensitive path pattern.",
                matched_rule="filesystem.sensitive_path_denied",
            )

        if not request.workspace_path:
            return None

        workspace = _normalize_path(request.workspace_path)
        if normalized == workspace or normalized.startswith(workspace + os.sep):
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason_code="PATH_WITHIN_WORKSPACE_ALLOWED",
                explanation=f"'{request.target}' is within workspace root '{request.workspace_path}'.",
                matched_rule="filesystem.within_workspace_allowed",
            )
        return PolicyResult(
            decision=PolicyDecision.DENY,
            reason_code="PATH_OUTSIDE_WORKSPACE_DENIED",
            explanation=f"'{request.target}' is outside workspace root '{request.workspace_path}'.",
            matched_rule="filesystem.outside_workspace_denied",
        )

    def _check_git_destructive(self, request: ActionRequest) -> Optional[PolicyResult]:
        """MA4.8 - not yet implemented. See _check_filesystem's docstring;
        the same "honest no-op, not a placeholder decision" reasoning
        applies to every stubbed stage in this class."""
        return None

    def _check_network_egress(self, request: ActionRequest) -> Optional[PolicyResult]:
        """MA4.6 - governs NETWORK_ACCESS and LLM_NETWORK_ACCESS only. Note
        this stage governs the DECISION layer only; kriya/core/llm.py's
        is_local_url/EgressViolationError enforcement (MA4.3) is a separate,
        independent hard boundary this stage's rules can never substitute
        for - see kriya/policy/__init__.py. This is why the local/non-local
        check below deliberately reuses kriya.core.llm.is_local_url itself
        (a deferred import - kriya/core/llm.py imports this module at
        module level, so importing it back at module level here would be
        circular; a function-local import is safe since by the time this
        method is ever CALLED, both modules are already fully loaded) rather
        than a second, independently-written local/private-address check
        that could quietly drift from the real enforcement boundary's own
        definition of "local" and produce misleading audit telemetry.

        No config exists yet (MA4.15's job) to distinguish "known public
        registry/lookup" from "arbitrary URL" (section 22) for plain
        NETWORK_ACCESS, so - matching section 12's "never silently default
        to ALLOW" - only a local/private target gets a real ALLOW here;
        every non-local NETWORK_ACCESS or LLM_NETWORK_ACCESS gets an
        explicit, specific DENY (not a fall-through to the generic
        backstop) so the audit log at least shows WHICH stage denied it."""
        if request.action_type not in (ActionType.NETWORK_ACCESS, ActionType.LLM_NETWORK_ACCESS):
            return None
        if not request.network_target:
            return None

        from kriya.core.llm import is_local_url

        target_is_local = is_local_url(request.network_target)

        if request.action_type == ActionType.LLM_NETWORK_ACCESS:
            if target_is_local:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason_code="LOCAL_LLM_ENDPOINT_ALLOWED",
                    explanation=f"'{request.network_target}' resolves to a local/private address.",
                    matched_rule="network_egress.local_llm_endpoint_allowed",
                )
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="NETWORK_TARGET_DENIED",
                explanation=(
                    f"'{request.network_target}' is not a local/private address; non-local LLM "
                    "endpoints are denied by this stage (kriya/core/llm.py's own egress check is "
                    "the real, independent enforcement boundary regardless of this decision)."
                ),
                matched_rule="network_egress.non_local_llm_denied",
            )

        if target_is_local:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason_code="LOCAL_NETWORK_TARGET_ALLOWED",
                explanation=f"'{request.network_target}' resolves to a local/private address.",
                matched_rule="network_egress.local_network_target_allowed",
            )
        return PolicyResult(
            decision=PolicyDecision.DENY,
            reason_code="NETWORK_TARGET_DENIED",
            explanation=(
                f"'{request.network_target}' is not a local/private address, and no config-driven "
                "public-lookup allowlist exists yet (MA4.15) to authorize it."
            ),
            matched_rule="network_egress.non_local_network_target_denied",
        )

    def _check_package_supply_chain(self, request: ActionRequest) -> Optional[PolicyResult]:
        """MA4.7 - not yet implemented."""
        return None

    def _check_command_allowlist(self, request: ActionRequest) -> Optional[PolicyResult]:
        """MA4.4 - only governs ActionType.RUN_COMMAND; git commands go
        through GIT_READ/GIT_WRITE (default-allow for reads at MA4.2,
        MA4.8's own git-write rules), never through this stage. Reasons from
        parsed command shape (executable + leading subcommand tokens), never
        raw substring matching (section 17: no `if "rm" in command`)."""
        if request.action_type != ActionType.RUN_COMMAND or not request.command:
            return None

        command = request.command
        executable = os.path.basename(command[0])

        if executable == "sudo":
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="COMMAND_SUDO_DENIED",
                explanation="Commands invoked via 'sudo' are denied unconditionally.",
                matched_rule="command_allowlist.sudo_denied",
            )

        if executable in _SHELL_WRAPPER_EXECUTABLES and "-c" in command:
            return PolicyResult(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason_code="COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL",
                explanation=(
                    f"'{executable} -c' can execute arbitrary nested commands and is not "
                    "itself allowlistable; requires approval."
                ),
                matched_rule="command_allowlist.shell_wrapper",
                requires_approval=True,
            )

        for prefix in _ALLOWLISTED_COMMAND_PREFIXES:
            if _command_matches_prefix(command, prefix):
                return PolicyResult(
                    decision=PolicyDecision.ALLOW_SANDBOXED,
                    reason_code="COMMAND_ALLOWLISTED",
                    explanation=f"'{' '.join(prefix)}' matches the MA4.4 starter build/test allowlist.",
                    matched_rule="command_allowlist.allowlisted",
                    requires_sandbox=True,
                )

        return PolicyResult(
            decision=PolicyDecision.DENY,
            reason_code="COMMAND_NOT_ALLOWLISTED",
            explanation=(
                f"'{' '.join(command)}' does not match any entry in the MA4.4 starter "
                "command allowlist."
            ),
            matched_rule="command_allowlist.not_allowlisted",
        )

    def _check_approval_rules(self, request: ActionRequest) -> Optional[PolicyResult]:
        """MA4.9 - not yet implemented."""
        return None

    def _default_policy(self, request: ActionRequest) -> PolicyResult:
        """Section 12's fail-closed backstop: never silently default to
        ALLOW. Only the two inherently read-only action types default-allow
        here; every other action type - including ones a later stage will
        eventually classify as safe (e.g. `mvn test` under MA4.4) - denies
        by default until that stage actually exists to say otherwise. This
        is intentionally strict for an engine nothing calls yet (MA4.2's own
        "Behavior Change: None in audit" scope) and stays strict as later
        stages are added one at a time - each new stage only ever ADDS a
        narrower ALLOW/ALLOW_SANDBOXED/REQUIRE_APPROVAL ahead of this
        backstop, it never has to loosen this method itself."""
        if request.action_type in _DEFAULT_ALLOW_ACTION_TYPES:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason_code="DEFAULT_READ_ONLY_ALLOWED",
                explanation=(
                    f"'{request.action_type.value}' is inherently read-only and no "
                    "earlier stage denied it; allowed by default."
                ),
                matched_rule="default_policy.read_only_allow",
            )
        return PolicyResult(
            decision=PolicyDecision.DENY,
            reason_code="DEFAULT_UNKNOWN_ACTION_DENIED",
            explanation=(
                f"No policy stage recognized '{request.action_type.value}' as "
                "allowed; denying by default (fail closed)."
            ),
            matched_rule="default_policy.unknown_denied",
        )
