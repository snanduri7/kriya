"""Policy telemetry - MA4.14 of the control-plane implementation plan (see
kriya/policy/__init__.py for MA4's overall principle).

Every MA4.3-4.13 real call site already logs its decision via a plain
`logger.debug(...)` string. MA4.14 gives that signal a stable, structured
shape instead - a PolicyDecisionRecord any future consumer (a real
persistence layer, a `kriya policy telemetry` CLI surface, an eval harness)
can key off by field rather than parsing log text - and, more importantly,
an explicit redaction discipline for it: a record must never carry a
secret or a proprietary payload, even though the ActionRequest it's built
from was never guaranteed to be free of one itself (kriya/policy/model.py's
own ActionRequest.metadata docstring only asks callers nicely not to put
secrets there - it doesn't enforce it). This module is the enforcement.

Two decisions here are deliberate and load-bearing:

1. `request.metadata` is NEVER included in a PolicyDecisionRecord, at all -
   not scrubbed, not summarized, simply absent. It's arbitrary caller-
   supplied data (a version string today, something else tomorrow); trying
   to pattern-match every possible secret shape that could end up in a free-
   form dict is a losing game. The record only ever carries the small,
   closed set of fields ActionRequest itself defines structurally
   (action_type, target, command, network_target, decision fields) - fields
   whose SHAPE is known, so they can actually be scrubbed with confidence.

2. `scrub_potential_secrets()` is a narrow, high-confidence redaction - the
   same "high-confidence, avoid false-positiving" principle MA4.12's
   injection detector already established, applied here to a different
   problem. It redacts recognizable credential key=value pairs, Bearer
   headers, and a few well-known cloud/API token SHAPES (AWS access key
   IDs, OpenAI-style sk- keys, GitHub token prefixes). It deliberately does
   NOT blanket-redact "any long hex or base64-looking string" - a SHA-256
   manifest hash (kriya/policy/approved_sources.py) or a git commit SHA is
   exactly that shape and is legitimate, non-secret policy-relevant data;
   redacting it on sight would make telemetry less useful, not more safe.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from kriya.policy.model import ActionRequest, PolicyResult

_SUMMARY_MAX_CHARS = 300
_REDACTED = "***REDACTED***"

# Key=value / key: value shaped credentials - the key name is the signal,
# not the value's shape, so this catches an arbitrary secret regardless of
# what it looks like as long as it's named like one.
_KEY_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key|access[_-]?key|auth(?:orization)?|credential)s?"
    r"([:=]\s*)(\S+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+\S+")

# Well-known credential SHAPES, matched even with no adjacent key name.
_SHAPED_SECRET_PATTERNS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key ID
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),  # OpenAI-style API key
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub token prefixes
)


def scrub_potential_secrets(text: str) -> str:
    """Read-only: returns a new string, never mutates the input. Deliberately
    conservative - see module docstring for what this intentionally does
    NOT redact (long hex/base64 strings with no credential-shaped name or
    known token prefix, e.g. a SHA-256 hash or commit SHA)."""

    scrubbed = _KEY_VALUE_SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    scrubbed = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", scrubbed)
    for pattern in _SHAPED_SECRET_PATTERNS:
        scrubbed = pattern.sub(_REDACTED, scrubbed)
    return scrubbed


def _summarize(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return scrub_potential_secrets(value)[:_SUMMARY_MAX_CHARS]


@dataclass(frozen=True)
class PolicyDecisionRecord:
    """One ExecutionPolicy.evaluate() outcome, redacted and shaped for
    telemetry. Never carries request.metadata (see module docstring) or any
    field ActionRequest/PolicyResult don't already define."""

    timestamp: str
    action_type: str
    decision: str
    reason_code: str
    matched_rule: Optional[str]
    requires_sandbox: bool
    requires_approval: bool
    enforced: bool
    target_summary: Optional[str]
    command_summary: Optional[str]
    network_target_summary: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "action_type": self.action_type,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "matched_rule": self.matched_rule,
            "requires_sandbox": self.requires_sandbox,
            "requires_approval": self.requires_approval,
            "enforced": self.enforced,
            "target_summary": self.target_summary,
            "command_summary": self.command_summary,
            "network_target_summary": self.network_target_summary,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def build_decision_record(
    request: ActionRequest, result: PolicyResult, enforced: bool = False
) -> PolicyDecisionRecord:
    """Pure construction - no I/O, no logging, no persistence. A caller
    (e.g. WorkflowEngine._authorize_action, MA4.13) decides what to do with
    the record; this function's only job is building it safely."""

    command_text = " ".join(request.command) if request.command else None
    return PolicyDecisionRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        action_type=request.action_type.value,
        decision=result.decision.value,
        reason_code=result.reason_code,
        matched_rule=result.matched_rule,
        requires_sandbox=result.requires_sandbox,
        requires_approval=result.requires_approval,
        enforced=enforced,
        target_summary=_summarize(request.target),
        command_summary=_summarize(command_text),
        network_target_summary=_summarize(request.network_target),
    )
