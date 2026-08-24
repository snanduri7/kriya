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
only). Stages 2-5 and 7 (filesystem, git-destructive, network/egress,
package/supply-chain, risk/profile approval) remain stubs, owned by MA4.5
through MA4.9 respectively.

MA4.4 also gave ExecutionPolicy its first real caller: kriya/tools/
validate.py's PolymorphicValidator consults it in AUDIT mode before every
ProcessController.run() (see that file's _run_cmd_with_timeout) - logged
only, never gating, exactly like kriya/core/llm.py's MA4.3 integration. This
module itself still takes no config (constructor takes no arguments; config
wiring is MA4.15's job, added additively, not as a signature change - the
same "additive, not a signature change" precedent kriya/workflow/
process_profile.py's own docstring already established for MA2), and still
consults no LLM to reach a decision.
"""

import os
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
        """MA4.5 - not yet implemented. Deliberately returns None (no rule
        matched) rather than a placeholder ALLOW/DENY, so this stage stays
        an honest no-op until it has real path-scoped rules."""
        return None

    def _check_git_destructive(self, request: ActionRequest) -> Optional[PolicyResult]:
        """MA4.8 - not yet implemented. See _check_filesystem's docstring;
        the same "honest no-op, not a placeholder decision" reasoning
        applies to every stubbed stage in this class."""
        return None

    def _check_network_egress(self, request: ActionRequest) -> Optional[PolicyResult]:
        """MA4.6 - not yet implemented. Note this stage governs the
        DECISION layer only; kriya/core/llm.py's is_local_url/
        EgressViolationError enforcement (MA4.3) is a separate, independent
        hard boundary that this stage's future rules can never substitute
        for - see kriya/policy/__init__.py."""
        return None

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
