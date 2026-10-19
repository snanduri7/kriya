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

1. `request.metadata` is NEVER included in a PolicyDecisionRecord AS A
   BLANKET DUMP - not scrubbed, not summarized, simply absent. It's
   arbitrary caller-supplied data (a version string today, something else
   tomorrow); trying to pattern-match every possible secret shape that
   could end up in a free-form dict is a losing game. The record only ever
   carries the small, closed set of fields ActionRequest itself defines
   structurally (action_type, target, command, network_target, decision
   fields) - fields whose SHAPE is known, so they can actually be scrubbed
   with confidence.

   TOOL-002 P1's ONE narrow, explicit exception (still consistent with
   this principle, not a violation of it): `metadata["mcp_tool_identity"]`
   is not arbitrary caller data, it's always exactly a `MCPToolIdentity` -
   a plain, closed, three-string dataclass this module already knows the
   full shape of (kriya/policy/model.py) - so its three fields are
   extracted and summarized individually, by name, the same way `target`/
   `command`/`network_target` already are; the metadata MAPPING itself is
   still never blanket-included. TOOL-003 P1 (2026-09-13) adds a second,
   equally narrow exception the same way: `metadata["mcp_capability_
   profile_identity"]` is always exactly an `MCPCapabilityProfileIdentity`
   (also a plain, closed, two-string dataclass) - exposed for audit
   context only, per Task 10's own instruction that TOOL-002 must never
   independently reinterpret the profile it merely carries along.
   TOOL-002 P2 (2026-09-13) adds a third, equally narrow exception:
   `metadata["mcp_containment_identity"]` is always exactly an
   `MCPContainmentIdentity` (a closed, three-field dataclass) - audit
   context only, describing a containment decision this module never
   reasons about (that decision is made entirely, independently, at MCP
   server start time - see kriya/mcp/mcp.py).

2. Redaction is NOT decided here (S2, OI-98): every summarized field goes
   through the one redaction owner, kriya/foundation/redaction.py::
   redact_text() - the same narrow, high-confidence rules this module used
   to own (credential-named key=value pairs, Bearer headers, well-known
   token shapes; SHA-256 digests and commit SHAs stay readable) plus URL
   userinfo, which the old local scrubber let through.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from kriya.foundation.redaction import redact_text
from kriya.policy.model import (
    ActionRequest,
    MCPCapabilityProfileIdentity,
    MCPContainmentIdentity,
    MCPToolIdentity,
    PolicyResult,
)

_SUMMARY_MAX_CHARS = 300


def _summarize(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return redact_text(value)[:_SUMMARY_MAX_CHARS]


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
    # TOOL-002 P1 - populated only for ActionType.MCP_TOOL_CALL (None
    # otherwise); see the module docstring's point 1 for why extracting
    # these three named fields from metadata is a narrow, explicit
    # exception rather than a blanket metadata dump.
    mcp_server_identity: Optional[str] = None
    mcp_tool_name_summary: Optional[str] = None
    mcp_schema_digest_short: Optional[str] = None
    # TOOL-003 P1 - populated only when the request carries an
    # MCPCapabilityProfileIdentity (None otherwise); audit context only,
    # never an input to any policy decision (see module docstring).
    mcp_capability_profile_digest_short: Optional[str] = None
    # TOOL-002 P2 (Task 12) - populated only when the request carries an
    # MCPContainmentIdentity (None otherwise); audit context only, same
    # narrow-named-field exception as the two fields above - never a
    # blanket metadata dump.
    mcp_containment_required: Optional[bool] = None
    mcp_containment_active: Optional[bool] = None
    mcp_containment_backend: Optional[str] = None

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
            "mcp_server_identity": self.mcp_server_identity,
            "mcp_tool_name_summary": self.mcp_tool_name_summary,
            "mcp_schema_digest_short": self.mcp_schema_digest_short,
            "mcp_capability_profile_digest_short": self.mcp_capability_profile_digest_short,
            "mcp_containment_required": self.mcp_containment_required,
            "mcp_containment_active": self.mcp_containment_active,
            "mcp_containment_backend": self.mcp_containment_backend,
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

    identity = request.metadata.get("mcp_tool_identity")
    mcp_server_identity = mcp_tool_name_summary = mcp_schema_digest_short = None
    if isinstance(identity, MCPToolIdentity):
        mcp_server_identity = _summarize(identity.server_identity)
        mcp_tool_name_summary = _summarize(identity.tool_name)
        mcp_schema_digest_short = identity.schema_digest[:12]

    capability_identity = request.metadata.get("mcp_capability_profile_identity")
    mcp_capability_profile_digest_short = None
    if isinstance(capability_identity, MCPCapabilityProfileIdentity):
        mcp_capability_profile_digest_short = capability_identity.profile_digest[:12]

    containment_identity = request.metadata.get("mcp_containment_identity")
    mcp_containment_required = mcp_containment_active = mcp_containment_backend = None
    if isinstance(containment_identity, MCPContainmentIdentity):
        mcp_containment_required = containment_identity.required
        mcp_containment_active = containment_identity.active
        mcp_containment_backend = containment_identity.backend

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
        mcp_server_identity=mcp_server_identity,
        mcp_tool_name_summary=mcp_tool_name_summary,
        mcp_schema_digest_short=mcp_schema_digest_short,
        mcp_capability_profile_digest_short=mcp_capability_profile_digest_short,
        mcp_containment_required=mcp_containment_required,
        mcp_containment_active=mcp_containment_active,
        mcp_containment_backend=mcp_containment_backend,
    )
