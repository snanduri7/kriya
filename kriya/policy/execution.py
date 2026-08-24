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
allowlist config exists yet). MA4.7 filled in stage 5 (package/supply-chain,
INSTALL_PACKAGE only - a URL/SCP-shaped source denies outright, everything
else well-formed requires approval, never a bare ALLOW). MA4.8 filled in
stage 3 (git-destructive, GIT_WRITE only - force-push and protected-ref
deletion hard-deny, config/remote mutation hard-deny, an ordinary push
weighted LIGHT allows (matching today's actual unrestricted behavior,
since Kriya never pushes on its own) while STANDARD/HEAVY or unknown weight
requires approval, everything else requires approval). MA4.9 filled in
stage 7 (risk/profile approval - trusts request.process_profile.
human_review_required and request.engineering_route.max_observed_risk_class
exactly as given, never reads config itself; only ever ADDS an approval
requirement on top of whatever the backstop would otherwise decide, never
grants a bare ALLOW). Every stage now has real logic - MA4.10 onward
(trust model, approved sources, injection detection, failure mapping,
telemetry, enforcement-mode validation) build on top of this engine rather
than filling in more of it.

MA4.4 gave ExecutionPolicy its first real caller (kriya/tools/validate.py's
PolymorphicValidator, before every ProcessController.run() - MA4.7 reuses
this SAME call site, issuing a second, INSTALL_PACKAGE-classified audit
request alongside the existing RUN_COMMAND one whenever the real command
looks like a package install); MA4.5 added a second real caller
(kriya/workflow/edit_safety.py's atomic_write_file); MA4.6 added a third
(kriya/tools/web.py's fetch_url_text); MA4.8 added a fourth
(kriya/workflow/worktree.py's create_git_worktree - the ONE real GIT_WRITE
Kriya's pipeline performs today, an empty bootstrap commit for a
zero-commit repo); MA4.9 added a fifth (kriya/workflow/workflow.py's own
existing MA2 approval-gate computation, the one real place
WorkflowControlContext - pairing a real EngineeringRoute with its resolved
ProcessProfile - is already in scope, so stage 7 has real, non-None input
to reason about for at least one caller) - all audit-only: logged, never
gating, exactly like kriya/core/llm.py's MA4.3 integration.
MA4.15 closed the loop: the constructor gained one optional, additive
parameter (sensitive_path_patterns - see ExecutionPolicy's own docstring)
so a real caller with config access (WorkflowEngine) can hand in
AutonomyConfig.sensitive_paths instead of drifting from the hardcoded
default; ExecutionPolicy() with no arguments is still every other call
site's exact construction, unchanged. This module itself still never
imports or reads kriya.config directly, and still consults no LLM to reach
a decision - MA4.15's actual AUDIT-vs-ENFORCE mode switch
(kriya.config.config.ExecutionPolicyConfig) lives in the callers
(WorkflowEngine._authorize_action's `enforce` argument), never inside this
engine.
"""

import os
import re
from typing import Optional, Sequence, Tuple

from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.workflow.triage import ExecutionWeight, RiskClass

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


# MA4.7 - package-manager INSTALL verbs (section 26's own examples, minus
# Maven: Kriya never shells out an explicit "install this new coordinate"
# command for Maven - dependency resolution happens implicitly during
# `mvn compile`/`test`, which section 27 explicitly calls "normal sandboxed
# build behavior," not a supply-chain action this stage should intercept).
_INSTALL_COMMAND_PREFIXES: Tuple[Tuple[str, ...], ...] = (
    ("npm", "install"),
    ("bundle", "install"),
    ("gem", "install"),
    ("cargo", "add"),
)

# A URL-shaped or SCP-style git target is exactly section 27's "unknown
# external package source" - a registry name/version spec is not.
_EXTERNAL_SOURCE_PATTERN = re.compile(r"(https?|ssh|git\+https?|git\+ssh)://|(^|\s)git@")


def extract_install_package_target(command: Tuple[str, ...]) -> Optional[str]:
    """MA4.7 - detects a package-manager install invocation from real
    command shape (section 26: "introduce ActionType.INSTALL_PACKAGE rather
    than depending only on command parsing" - the DETECTION here is still
    necessarily shape-based, since Kriya only ever installs packages by
    shelling out; what changes is that a match gets routed to its own
    dedicated supply-chain policy stage instead of blending into the
    generic command-allowlist stage's COMMAND_NOT_ALLOWLISTED signal).
    Handles a venv-qualified pip invocation too (`<python> -m pip install
    ...`, kriya/tools/validate.py's own real shape for _ensure_project_venv),
    not just a bare `pip`/`pip3 install`.

    Returns a short, audit-readable label for the install target (the
    trailing arguments, space-joined) - NOT a precisely parsed single
    package name/version; good enough for telemetry and this stage's
    URL-vs-registry-name check, not meant for exact-match logic. Returns
    None when `command` isn't an install invocation, or is one with no
    trailing arguments at all (e.g. a bare `bundle install` with nothing
    else to label - not itself suspicious, just nothing for this stage's
    ActionRequest.target to carry)."""
    if not command:
        return None
    executable = os.path.basename(command[0])
    rest = command[1:]

    if executable in ("pip", "pip3") and rest[:1] == ("install",):
        return " ".join(rest[1:]) or None
    if (
        executable.startswith("python") and len(rest) >= 3
        and rest[0] == "-m" and rest[1] in ("pip", "pip3") and rest[2] == "install"
    ):
        return " ".join(rest[3:]) or None

    for prefix in _INSTALL_COMMAND_PREFIXES:
        if _command_matches_prefix(command, prefix):
            return " ".join(command[len(prefix):]) or None

    return None

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
# is in play. This is the DEFAULT list, used whenever a caller constructs
# ExecutionPolicy() with no override - it duplicated kriya.config.config.
# AutonomyConfig.sensitive_paths's baseline patterns until MA4.15, which
# added ExecutionPolicy.__init__'s optional sensitive_path_patterns
# parameter specifically so a real caller with config access (today: only
# WorkflowEngine, the one real construction site that already has
# self.kernel.config in scope) can hand in AutonomyConfig.sensitive_paths
# directly instead of drifting from it. ExecutionPolicy itself still never
# imports kriya.config or reads it directly (MA4.2's own principle intact -
# this is a caller-supplied override, not the engine acquiring a config
# dependency); the other real callers (llm.py, validate.py, edit_safety.py,
# web.py, worktree.py) still construct ExecutionPolicy() with no override
# and get this same default list.
_DEFAULT_SENSITIVE_PATH_PATTERN_STRINGS: Tuple[str, ...] = (
    r"(^|/)\.ssh(/|$)",
    r"(^|/)\.aws(/|$)",
    r"(^|/)\.kube(/|$)",
    r"(^|/)\.gnupg(/|$)",
    r"\.env$",
    r"credentials",
    r"secrets",
    r"password",
)


def _compile_path_patterns(pattern_strings: Sequence[str]) -> Tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in pattern_strings)


def _is_sensitive_path(normalized_path: str, patterns: Tuple[re.Pattern, ...]) -> bool:
    return any(p.search(normalized_path) for p in patterns)


def _normalize_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


# MA4.8 - git-write classification, all effect-based (section 31: "requires
# effect-based protection rather than matching only one command spelling"),
# never a single literal string match.
_GIT_FORCE_PUSH_FLAGS = frozenset({"--force", "-f"})
_GIT_DELETE_FLAGS = frozenset({"--delete", "-d", "-D"})
# Deliberately small and generic (not read from this repo's own real branch
# name, which this stage has no way to know) - "start small" per MA4.4's own
# precedent; a real project's actual default branch is config territory
# (MA4.15), not something this stage can discover on its own.
_GIT_PROTECTED_REFS = frozenset({"main", "master"})
_GIT_MUTATING_REMOTE_VERBS = frozenset({"set-url", "remove", "rm", "add", "rename", "set-head"})


def _git_subcommand_and_args(command: Tuple[str, ...]) -> Tuple[Optional[str], Tuple[str, ...]]:
    args = list(command)
    if args and os.path.basename(args[0]) == "git":
        args = args[1:]
    if not args:
        return None, ()
    return args[0], tuple(args[1:])


def _is_force_push(rest: Tuple[str, ...]) -> bool:
    return any(a in _GIT_FORCE_PUSH_FLAGS or a.startswith("--force-with-lease") for a in rest)


def _targets_protected_ref(rest: Tuple[str, ...]) -> bool:
    positional = [a for a in rest if not a.startswith("-")]
    return any(a in _GIT_PROTECTED_REFS for a in positional)


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
    consulted to decide whether an action is allowed.

    MA4.15 - the constructor takes one optional, additive parameter
    (sensitive_path_patterns) rather than a config object: ExecutionPolicy
    still never imports or reads kriya.config itself. ExecutionPolicy()
    with no arguments - every call site's exact current construction,
    unchanged - keeps using the same hardcoded default list stage 2 always
    has."""

    def __init__(self, sensitive_path_patterns: Optional[Sequence[str]] = None) -> None:
        self._sensitive_path_patterns = _compile_path_patterns(
            sensitive_path_patterns if sensitive_path_patterns else _DEFAULT_SENSITIVE_PATH_PATTERN_STRINGS
        )

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
        if _is_sensitive_path(normalized, self._sensitive_path_patterns):
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
        """MA4.8 - governs GIT_WRITE only (GIT_READ default-allows at MA4.2's
        own backstop, untouched here). Every rule reasons from parsed
        command shape/effect (section 31), never a single literal spelling:
        a force-push denies via ANY of --force/-f/--force-with-lease(=...),
        not just one exact flag string; a branch/ref deletion denies only
        when the ref being deleted is a recognized protected name, not
        merely because -D was used.

        `git push` gets its own weight-sensitive rule (section 33): LIGHT
        allows (today's actual behavior is unrestricted, since Kriya never
        pushes on its own - see kriya/workflow/worktree.py's own MA4.8
        integration note for the one real GIT_WRITE call site that exists);
        STANDARD/HEAVY, or no route to weigh at all, requires approval.
        `git config` and a mutating `git remote` verb (set-url/remove/rm/
        add/rename/set-head - section 30's "remote modification"/"git
        config mutation") hard-deny unconditionally, no approval path, per
        section 32. Everything else well-formed GIT_WRITE (an ordinary
        commit, tag, merge, non-force push already handled above, a
        non-protected branch delete) requires approval - never a bare
        ALLOW, mirroring MA4.7's INSTALL_PACKAGE precedent."""
        if request.action_type != ActionType.GIT_WRITE or not request.command:
            return None

        subcommand, rest = _git_subcommand_and_args(request.command)
        if subcommand is None:
            return None

        if subcommand == "push":
            if _is_force_push(rest):
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason_code="GIT_FORCE_PUSH_DENIED",
                    explanation="Force-push variants are denied unconditionally.",
                    matched_rule="git_destructive.force_push_denied",
                )
            if any(f in _GIT_DELETE_FLAGS for f in rest) and _targets_protected_ref(rest):
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason_code="PROTECTED_REF_MUTATION_DENIED",
                    explanation="Deleting a protected ref via push is denied unconditionally.",
                    matched_rule="git_destructive.protected_ref_push_delete_denied",
                )
            weight = request.engineering_route.execution_weight if request.engineering_route else None
            if weight == ExecutionWeight.LIGHT:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason_code="GIT_PUSH_ALLOWED_LIGHT",
                    explanation="Ordinary push under a LIGHT execution weight matches today's unrestricted behavior.",
                    matched_rule="git_destructive.push_allowed_light",
                )
            return PolicyResult(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason_code="GIT_PUSH_REQUIRES_APPROVAL",
                explanation="Push requires approval outside a LIGHT execution weight (or with no route to weigh).",
                matched_rule="git_destructive.push_requires_approval",
                requires_approval=True,
            )

        if subcommand == "branch" and any(f in _GIT_DELETE_FLAGS for f in rest):
            if _targets_protected_ref(rest):
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason_code="PROTECTED_REF_MUTATION_DENIED",
                    explanation="Deleting a protected branch is denied unconditionally.",
                    matched_rule="git_destructive.protected_branch_delete_denied",
                )
            return PolicyResult(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason_code="GIT_WRITE_REQUIRES_APPROVAL",
                explanation="Deleting a non-protected branch requires approval.",
                matched_rule="git_destructive.branch_delete_requires_approval",
                requires_approval=True,
            )

        if subcommand == "config":
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="GIT_CONFIG_MUTATION_DENIED",
                explanation="git config mutation is denied unconditionally.",
                matched_rule="git_destructive.config_mutation_denied",
            )

        if subcommand == "remote" and rest and rest[0] in _GIT_MUTATING_REMOTE_VERBS:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="GIT_REMOTE_MUTATION_DENIED",
                explanation=f"'git remote {rest[0]}' is denied unconditionally.",
                matched_rule="git_destructive.remote_mutation_denied",
            )

        return PolicyResult(
            decision=PolicyDecision.REQUIRE_APPROVAL,
            reason_code="GIT_WRITE_REQUIRES_APPROVAL",
            explanation=f"'git {subcommand}' is a write operation and requires approval.",
            matched_rule="git_destructive.ordinary_write_requires_approval",
            requires_approval=True,
        )

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
        """MA4.7 - governs INSTALL_PACKAGE only. Per section 27/28: MA4 does
        not implement full SCA (no SBOM, license scan, CVE scanner, package
        reputation) - the essential improvement is that a new dependency
        becomes an explicit, policy-controlled action at all, not that this
        stage judges the package itself. A URL/SCP-shaped target (an
        arbitrary source, not a registry name+version) denies outright;
        everything else well-formed requires approval - never a bare ALLOW,
        since "the build already had this dependency declared" isn't
        something this stage can distinguish from "the agent just added a
        new one" without config this task doesn't have (MA4.15)."""
        if request.action_type != ActionType.INSTALL_PACKAGE or not request.target:
            return None

        if _EXTERNAL_SOURCE_PATTERN.search(request.target):
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="UNKNOWN_PACKAGE_SOURCE_DENIED",
                explanation=f"'{request.target}' names an arbitrary URL/git source, not a registry package.",
                matched_rule="package_supply_chain.unknown_source_denied",
            )

        return PolicyResult(
            decision=PolicyDecision.REQUIRE_APPROVAL,
            reason_code="PACKAGE_INSTALL_REQUIRES_APPROVAL",
            explanation=f"Installing '{request.target}' is a supply-chain action and requires approval.",
            matched_rule="package_supply_chain.requires_approval",
            requires_approval=True,
        )

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
        """MA4.9 - preserves MA2's STANDARD/HEAVY approval-requirement rule
        (design doc section 35) as a policy-level decision, WITHOUT any
        config dependency of its own: this stage trusts
        request.process_profile.human_review_required and
        request.engineering_route.max_observed_risk_class exactly as given,
        the same way stages 2-6 already trust request.workspace_path/
        network_target/command/etc. Whether to actually POPULATE those two
        fields for a real call is the CALLER's responsibility (and, for a
        real production caller, itself requires
        process_profiles.enabled/enforce_approval per MA2's own config
        gating - kriya/workflow/workflow.py's process_profile_requires_review
        computation) - this stage has and needs no config of its own.

        Only ever ADDS an approval requirement, never grants a bare ALLOW -
        a LIGHT profile (human_review_required=False) and a non-HIGH risk
        class simply return None here (no opinion), falling through to
        whatever stage 8's backstop would have decided anyway. Since stage
        6 already owns RUN_COMMAND unconditionally and stages 2/4/5/3
        already own their own action types before this stage ever runs
        (section 11's fixed order), this stage's real effect today is
        narrow - a WRITE_FILE request that reached here without a
        workspace_path, or a PUBLISH_ARTIFACT request - but is real and
        directly testable against the ACTUAL ProcessProfile objects MA2
        already uses (LIGHT_PROFILE/STANDARD_PROFILE/HEAVY_PROFILE), not a
        hand-rolled stand-in, per section 35's "only remove duplicate logic
        after tests prove parity" migration note."""
        if request.process_profile is not None and request.process_profile.human_review_required:
            return PolicyResult(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason_code="PROCESS_PROFILE_REQUIRES_APPROVAL",
                explanation="The resolved ProcessProfile for this request marks human_review_required=True.",
                matched_rule="approval_rules.process_profile_requires_approval",
                requires_approval=True,
            )
        if (
            request.engineering_route is not None
            and request.engineering_route.max_observed_risk_class == RiskClass.HIGH
        ):
            return PolicyResult(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason_code="HIGH_RISK_REQUIRES_APPROVAL",
                explanation="The request's EngineeringRoute has observed HIGH risk at some point this run.",
                matched_rule="approval_rules.high_risk_requires_approval",
                requires_approval=True,
            )
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
