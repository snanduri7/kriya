"""ExecutionPolicy - the deterministic decision engine itself. MA4.2 of the
control-plane implementation plan (see kriya/policy/__init__.py for MA4's
overall principle; kriya/policy/model.py for the MA4.1 domain types this
engine consumes and returns).

MA4.2 scope only, per the MA4 design doc's own task breakdown ("ExecutionPolicy
deterministic engine - Behavior Change: None in audit"): the fixed
evaluate() pipeline and its stage ORDER, plus the platform-invariants stage
(1) and the terminal default-policy stage (8). Stages 2-7 (filesystem, git
destructive-operation, network/egress, package/supply-chain, command
allowlist, risk/profile approval) are deliberately stubbed here - each
returns None (no rule matched, fall through) - their real rule content is
scoped to MA4.4 through MA4.9 respectively, landing as separate, individually
verified commits. Filling them in early would mean shipping unreviewed
security rules in the same commit as the engine skeleton; keeping them
empty here means every one of those later tasks is a pure addition to an
already-tested pipeline, never a rewrite of it.

Nothing calls ExecutionPolicy from a real Kriya code path yet - that wiring
starts at MA4.4. This module is fully isolated and side-effect-free:
deterministic in, deterministic out, no LLM, no filesystem access, no
network access, no config dependency (constructor takes no arguments; config
wiring is MA4.15's job, added additively, not as a signature change - the
same "additive, not a signature change" precedent kriya/workflow/
process_profile.py's own docstring already established for MA2).
"""

from typing import Optional, Tuple

from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult

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
        """MA4.4 - not yet implemented."""
        return None

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
