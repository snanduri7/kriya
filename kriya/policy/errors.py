"""PolicyDeniedError - MA4.13 of the control-plane implementation plan (see
kriya/policy/__init__.py for MA4's overall principle).

Raised only by an ENFORCING policy consultation (kriya/workflow/workflow.py's
_authorize_action, called with enforce=True) - never by ExecutionPolicy.
evaluate() itself, which only ever returns a PolicyResult and raises
nothing. No real Kriya call site passes enforce=True today (MA4.15's config
wiring is what will eventually let one); this exception exists so that
code path is real and testable now rather than invented later under
pressure once enforcement is actually turned on somewhere."""

from kriya.policy.model import ActionRequest, PolicyResult


class PolicyDeniedError(Exception):
    """Carries the exact request and verdict that caused the denial, so a
    caller (or a human-facing error message) can explain WHY without
    re-deriving it. `str(error)` is a one-line summary; the structured
    `request`/`result` fields are for callers that want to branch on
    `result.reason_code` or `result.decision` (DENY vs. a rejected
    REQUIRE_APPROVAL) rather than parse text."""

    def __init__(self, request: ActionRequest, result: PolicyResult):
        self.request = request
        self.result = result
        super().__init__(
            f"Policy denied {request.action_type.value}: {result.reason_code} - {result.explanation}"
        )
