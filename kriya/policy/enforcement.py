"""Narrow, explicitly-authorized real enforcement for a fixed set of
ExecutionPolicy hard invariants - MA7.3 of the MA7 hardening plan. Mirrors
kriya/policy/filesystem.py's AuthorizedFileWriter precedent exactly, same
reasoning: MA4's general audit-only mandate stays fully intact
(kriya.config.config.ExecutionPolicyConfig's validator still rejects
execution_policy.mode="enforce" - this module does not touch that
restriction, and never will on its own), but a small, explicitly-named set
of DENY reason_codes - each one a "this is always either a bug or an
attack, never a legitimate Kriya action a human needs to judge" case, per
AuthorizedFileWriter's own docstring - really refuses rather than merely
logging. Confirmed with the user directly before implementing (2026-08-24),
same as AuthorizedFileWriter's own precedent.

Deliberately NOT "every DENY decision from ExecutionPolicy.evaluate()":
COMMAND_NOT_ALLOWLISTED (MA4.4's narrow starter allowlist) denies plenty of
real, legitimate build/test commands Kriya already runs across supported
stacks that simply aren't on that short list yet - enforcing that reason_code
would break real generation runs, not just attacks or bugs. Only reason_codes
that are NEVER legitimate, regardless of stack/project/command shape, are in
HARD_ENFORCED_REASON_CODES below. REQUIRE_APPROVAL decisions (ordinary git
push, package installs, shell-wrapper `-c`, ...) are also left untouched -
turning those into a real gate needs a real approval-callback wired at the
calling site, a separate, larger decision this module does not make.

Real reachability, honestly noted (2026-08-24): as of this writing, neither
of this module's two real callers (kriya/workflow/worktree.py's bootstrap
`git commit --allow-empty`, kriya/tools/validate.py's Kriya-constructed
compile/test commands) can ever actually produce one of these reason_codes
in practice - GitTool (plugins/core_tools) has no push/branch-delete/config/
remote code path at all, and validate.py's commands are built by Kriya's
own stack-detection logic, never from untrusted model/tool output. This is
real defense-in-depth for if/when that changes, not the closure of a
currently-live gap (see kriya/workflow/workflow_controller.py's own
TOOL-subtask hard-stop, MA7's actual live gap, fixed separately)."""

from __future__ import annotations

from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, PolicyDecision, PolicyResult

HARD_ENFORCED_REASON_CODES = frozenset({
    "COMMAND_SUDO_DENIED",
    "GIT_FORCE_PUSH_DENIED",
    "PROTECTED_REF_MUTATION_DENIED",
    "GIT_CONFIG_MUTATION_DENIED",
    "GIT_REMOTE_MUTATION_DENIED",
})


def enforce_hard_invariants(policy: ExecutionPolicy, request: ActionRequest) -> PolicyResult:
    """Evaluates `request` through `policy` exactly as any audit-only
    caller already does (same PolicyResult returned, unchanged, still the
    caller's job to log) - but additionally RAISES PolicyDeniedError when
    the result is a DENY whose reason_code is in HARD_ENFORCED_REASON_CODES.
    Every other decision (ALLOW, ALLOW_SANDBOXED, REQUIRE_APPROVAL, or a
    DENY for any OTHER reason_code - e.g. COMMAND_NOT_ALLOWLISTED) is
    returned exactly as evaluate() produced it, still audit-only."""
    result = policy.evaluate(request)
    if result.decision == PolicyDecision.DENY and result.reason_code in HARD_ENFORCED_REASON_CODES:
        raise PolicyDeniedError(request=request, result=result)
    return result
