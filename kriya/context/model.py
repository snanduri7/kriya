"""Request, principal, workspace and run context (MODULE-CONTRACTS §2).

Frozen value types only: nothing here resolves a path from the environment,
the home directory or the process working directory. ``RequestContext.local``
takes its inputs explicitly; a ``None`` workspace means "no workspace", never
"the current directory".
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional, Tuple

from kriya.context.workspace_identity import canonical_workspace, workspace_identity
from kriya.foundation.cancellation import CancellationToken
from kriya.foundation.clock import Deadline
from kriya.foundation.ids import LaunchId, RequestId, RunId, WorkspaceId

ScopeKind = Literal["system", "organization", "user", "project", "run"]
SCOPE_KINDS: Tuple[str, ...] = ("system", "organization", "user", "project", "run")
LOCAL_PRINCIPAL_ID = "local"
LOCAL_PRINCIPAL_DISPLAY = "local operator"


class SecurityDomain(str, Enum):
    """The isolation domain a principal acts in. Local mode has one; per-user or
    per-organization domains are a future extension (provider cache isolation keys on it)."""

    LOCAL = "local"


@dataclass(frozen=True)
class PrincipalContext:
    id: str
    display: str
    security_domain: SecurityDomain

    @staticmethod
    def local() -> "PrincipalContext":
        """The single local principal: the operator of this machine."""
        return PrincipalContext(id=LOCAL_PRINCIPAL_ID, display=LOCAL_PRINCIPAL_DISPLAY, security_domain=SecurityDomain.LOCAL)


@dataclass(frozen=True)
class ScopeRef:
    """Where a scoped thing (configuration, skills, tools, evidence, state) comes from."""

    kind: ScopeKind
    id: str

    def __post_init__(self) -> None:
        if self.kind not in SCOPE_KINDS:
            raise ValueError(f"unknown scope kind {self.kind!r}; expected one of {SCOPE_KINDS}")
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("a scope needs a non-empty id")


@dataclass(frozen=True)
class WorkspaceContext:
    """One workspace: its persisted identity, its canonical root and the
    directories CONFIG resolved for it (``None`` until resolved: D3)."""

    workspace_id: WorkspaceId
    root: str
    config_dir: Optional[str] = None
    state_dir: Optional[str] = None

    @staticmethod
    def for_root(root: str, *, config_dir: Optional[str] = None, state_dir: Optional[str] = None) -> "WorkspaceContext":
        """The context of ``root``: the same canonical spelling and ``workspace_id``
        every control-plane record of that workspace carries."""
        return WorkspaceContext(workspace_id=WorkspaceId(workspace_identity(root)), root=canonical_workspace(root),
                                config_dir=config_dir, state_dir=state_dir)


@dataclass(frozen=True)
class RunScope:
    """The typed identity of one run. It is NOT the mutating run lease
    (``control.run_coordinator.RunContext`` keeps that role)."""

    run_id: RunId
    launch_id: Optional[LaunchId]
    workspace: WorkspaceContext
    principal: PrincipalContext
    config_fingerprint: str
    started_iso: str


@dataclass(frozen=True)
class RequestContext:
    """Built by the client/adapter boundary for every operation."""

    request_id: RequestId
    principal: PrincipalContext
    workspace: Optional[WorkspaceContext]
    run: Optional[RunScope]
    deadline: Optional[Deadline]
    cancellation: Optional[CancellationToken]
    config_scope: Tuple[ScopeRef, ...]
    skills_scope: Tuple[ScopeRef, ...]
    tools_scope: Tuple[ScopeRef, ...]
    evidence_scope: ScopeRef
    state_scope: ScopeRef

    @staticmethod
    def local(workspace_root: Optional[str], *, request_id: Optional[str] = None, config_dir: Optional[str] = None,
              state_dir: Optional[str] = None, deadline: Optional[Deadline] = None,
              cancellation: Optional[CancellationToken] = None) -> "RequestContext":
        """The context of one local request. ``workspace_root=None`` is a request
        about no workspace (e.g. ``capabilities``); it never falls back to the
        process working directory. Scopes: system and the local user for
        configuration, skills and tools, plus the project when a workspace is
        given; evidence and state live in the local user's state directory."""
        principal = PrincipalContext.local()
        workspace = WorkspaceContext.for_root(workspace_root, config_dir=config_dir, state_dir=state_dir) \
            if workspace_root is not None else None
        user = ScopeRef("user", principal.id)
        layered: Tuple[ScopeRef, ...] = (ScopeRef("system", "kriya"), user)
        if workspace is not None:
            layered = layered + (ScopeRef("project", workspace.workspace_id),)
        return RequestContext(request_id=RequestId(request_id or f"local-{uuid.uuid4().hex}"), principal=principal,
                              workspace=workspace, run=None, deadline=deadline, cancellation=cancellation,
                              config_scope=layered, skills_scope=layered, tools_scope=layered,
                              evidence_scope=user, state_scope=user)


__all__ = ["LOCAL_PRINCIPAL_DISPLAY", "LOCAL_PRINCIPAL_ID", "PrincipalContext", "RequestContext", "RunScope", "SCOPE_KINDS",
           "ScopeKind", "ScopeRef", "SecurityDomain", "WorkspaceContext"]
