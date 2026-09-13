"""Policy domain model - MA4.1 of the control-plane implementation plan (see
kriya/policy/__init__.py for MA4's overall principle).

MA4.1 scope only: the four-outcome decision enum, a closed action-type
vocabulary, and the two frozen request/result dataclasses that carry facts
between a call site and ExecutionPolicy.evaluate() (MA4.2, not yet added in
this module). No engine, no rules, no wiring into any real call site, no
behavior change - this module is pure data, importable with zero side
effects and zero new dependencies beyond kriya.workflow.triage and
kriya.workflow.process_profile's own existing domain types.
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from kriya.workflow.process_profile import ProcessProfile
from kriya.workflow.triage import EngineeringRoute


class PolicyDecision(str, Enum):
    """The four outcomes ExecutionPolicy.evaluate() may return. Deliberately
    kept as four distinct values, never collapsed - ALLOW_SANDBOXED is not
    ALLOW (it still requires sandboxed execution via ProcessController, per
    MA4's own defense-in-depth principle), and REQUIRE_APPROVAL is not DENY
    (it routes to the existing human-approval callback rather than refusing
    outright). Collapsing either pair would silently discard control
    behavior a downstream call site depends on."""

    ALLOW = "allow"
    ALLOW_SANDBOXED = "allow_sandboxed"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class ActionType(str, Enum):
    """A deliberately small, closed vocabulary of consequential actions
    ExecutionPolicy can reason about in MA4. A new class of consequential
    action introduced later must explicitly be added here and given real
    rule coverage in ExecutionPolicy.evaluate() - it may never be silently
    swept into an existing value just because it feels adjacent (e.g. a
    future "delete file" action is not WRITE_FILE)."""

    READ_FILE = "read_file"
    WRITE_FILE = "write_file"

    RUN_COMMAND = "run_command"

    NETWORK_ACCESS = "network_access"
    LLM_NETWORK_ACCESS = "llm_network_access"

    INSTALL_PACKAGE = "install_package"

    GIT_READ = "git_read"
    GIT_WRITE = "git_write"

    PUBLISH_ARTIFACT = "publish_artifact"

    # TOOL-002 P1: an MCP `tools/call` - see MCPToolIdentity below for the
    # structured identity this ActionType's own ExecutionPolicy stage binds
    # to. Deliberately its own ActionType, never folded into RUN_COMMAND or
    # any existing value - an MCP tool call is neither Kriya-authored
    # (RUN_COMMAND's own implicit trust assumption for its allowlisted
    # prefixes) nor semantically classifiable in advance the way the other
    # ActionTypes are (see docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md's
    # TOOL-002/TOOL-003 investigation entry for the full "opaque schema"
    # analysis behind this decision).
    MCP_TOOL_CALL = "mcp_tool_call"


@dataclass(frozen=True)
class MCPToolIdentity:
    """TOOL-002 P1 - the minimum Kriya-owned, structured identity needed to
    distinguish one MCP tool from another for an authority decision. Bound
    to at MCPTool construction time (kriya/mcp/mcp.py) and carried through
    ExecutionPolicy.evaluate() via ActionRequest.metadata (this dataclass's
    own instances, never a flattened string) - never re-derived from a
    collision-prone display name.

    Deliberately excludes the tool's `description` (Invariant: MCP
    metadata may describe an operation but may not authorize it - a
    server cannot change what it is by changing how it explains itself)
    and excludes tool ANNOTATIONS/result text entirely (never inputs to an
    authority decision at all). `schema_digest` participates in identity
    specifically so a schema change - which can silently change what
    arguments a tool actually accepts/does - invalidates whatever a prior
    evaluation assumed about this tool, without needing any broader
    cryptographic machinery (a plain SHA-256 over a canonicalized JSON
    Schema is sufficient for this - the threat model here is "detect
    drift/redirection deterministically," not "prevent a nation-state
    forgery," so nothing stronger is warranted).

    `server_identity` is the trusted `mcp.<server_name>` config key (itself
    SEC-009 SECURITY_AUTHORITY-governed) - untrusted content (the MCP
    server's own advertised `tool_meta['name']`/schema) can never influence
    THIS field, only `tool_name`/`schema_digest`. Frozen and hashable by
    construction (all three fields are plain strings) so it can be used
    directly as a set member (see ExecutionPolicy.__init__'s
    approved_mcp_tool_identities parameter) and as an ActionRequest.metadata
    value without any extra wrapping."""

    server_identity: str
    tool_name: str
    schema_digest: str


def compute_mcp_schema_digest(schema: Any) -> str:
    """TOOL-002 P1 - canonicalizes an MCP tool's JSON Schema (inputSchema)
    before hashing, so semantically-identical schemas produce the same
    digest regardless of key order, and so no `description` text (at any
    nesting depth - JSON Schema permits a `description` alongside
    `properties`/`items`/etc. at any level, not just the top level)
    contributes to the digest - Invariant: authority identity never
    includes description text, structural shape only. A plain SHA-256 over
    a deterministically-sorted, whitespace-free JSON serialization -
    deliberately no broader cryptographic machinery (signatures, HMACs)
    since the threat model this identity binding defends against is
    accidental/adversarial IDENTITY DRIFT (Phase 4/12/13/14 of the
    TOOL-002/003 investigation), not forgery of a digest Kriya itself
    always computes locally from data it already received directly over
    the same stdio connection - there is no untrusted digest input to
    forge against."""

    def _strip_descriptions(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: _strip_descriptions(v) for k, v in node.items() if k != "description"}
        if isinstance(node, list):
            return [_strip_descriptions(v) for v in node]
        return node

    canonical = json.dumps(_strip_descriptions(schema), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActionRequest:
    """Explicit facts a call site hands to ExecutionPolicy.evaluate() - never
    an arbitrary mutable workflow object. `metadata` exists for rule-specific
    extras that don't warrant a first-class field yet, but per MA4's own
    telemetry rule (see kriya/policy/execution.py once it exists) it must
    never carry secrets, full prompts, full repository content, or any other
    proprietary payload - only small, policy-relevant facts (e.g. a package
    name, a ref name)."""

    action_type: ActionType

    target: Optional[str] = None
    command: Optional[Tuple[str, ...]] = None
    network_target: Optional[str] = None
    workspace_path: Optional[str] = None

    engineering_route: Optional[EngineeringRoute] = None
    process_profile: Optional[ProcessProfile] = None

    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyResult:
    """The outcome of one ExecutionPolicy.evaluate() call. `reason_code`
    must stay stable and machine-readable (telemetry and tests key off it
    directly, the same convention kriya/workflow/milestone_validation.py's
    reason codes already established) - it is not free-form explanatory
    text, that's what `explanation` is for."""

    decision: PolicyDecision

    reason_code: str
    explanation: str

    matched_rule: Optional[str] = None

    requires_sandbox: bool = False
    requires_approval: bool = False
