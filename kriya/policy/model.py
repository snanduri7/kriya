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
