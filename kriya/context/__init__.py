"""CONTEXT (full modularization, slice B1; MODULE-CONTRACTS §2).

The typed identity of a request: who asks (``PrincipalContext``), which
workspace it is about (``WorkspaceContext``), which run (``RunScope``) and
where its scoped inputs come from (``ScopeRef``), bundled as one frozen
``RequestContext`` that the client/adapter boundary builds for every
operation. Local mode has exactly one principal.

``context.workspace_identity`` owns the stable workspace identity
(moved from ``kriya/control`` in B1; the persisted ``workspace_id`` values
are unchanged).

CONTEXT reads no environment variable, home directory or process working
directory: every input is explicit (``RequestContext.local(root, ...)``);
the state and configuration directories are resolved by CONFIG (D3) and
passed in. It imports only FOUNDATION and PLATFORM.
"""
from kriya.context.model import (
    PrincipalContext,
    RequestContext,
    RunScope,
    ScopeRef,
    SecurityDomain,
    WorkspaceContext,
)

__all__ = ["PrincipalContext", "RequestContext", "RunScope", "ScopeRef", "SecurityDomain", "WorkspaceContext"]
