"""``LocalKriyaClient`` (slice B4; MODULE-CONTRACTS §16): the in-process ``KriyaClient`` over ``kriya.application`` services.

The real desktop chain is: Electron renderer -> typed IPC -> Electron main/host -> the ``kriya kup-stdio`` child -> the KUP adapter
(``kriya/kup/cli_ops.py``: request -> ``client.<service>.<op>(ctx, ...)`` -> envelope) -> THIS client -> the application services ->
the domain modules. It is the only implementation; it adds no behaviour of its own: each service attribute IS the composed service, and
``context`` builds the request context of this client's principal (local mode: the one local operator).
"""
from __future__ import annotations

import dataclasses
from typing import Optional

from kriya.application.compose import ApplicationServices, compose
from kriya.context import PrincipalContext, RequestContext

LOCAL_PRINCIPAL = PrincipalContext.local()


class LocalKriyaClient:
    def __init__(self, services: ApplicationServices, *, principal: PrincipalContext = LOCAL_PRINCIPAL) -> None:
        self._services = services
        self._principal = principal

    @property
    def principal(self) -> PrincipalContext:
        return self._principal

    # The service attributes (KriyaClient): the composed services themselves, built on first use.
    projects = property(lambda self: self._services.projects)
    runs = property(lambda self: self._services.runs)
    reviews = property(lambda self: self._services.reviews)
    configuration = property(lambda self: self._services.configuration)
    models = property(lambda self: self._services.models)
    skills = property(lambda self: self._services.skills)
    tools = property(lambda self: self._services.tools)
    evidence = property(lambda self: self._services.evidence)
    history = property(lambda self: self._services.history)
    health = property(lambda self: self._services.health)

    def context(self, *, workspace_root: Optional[str], request_id: Optional[str] = None) -> RequestContext:
        """The context of one request of this client: ``RequestContext.local`` (never the process working directory), with this principal."""
        ctx = RequestContext.local(workspace_root, request_id=request_id)
        return ctx if ctx.principal == self._principal else dataclasses.replace(ctx, principal=self._principal)


def local_client() -> LocalKriyaClient:
    """A client over a fresh composition (the KUP adapter builds one per request: nothing is shared between requests)."""
    return LocalKriyaClient(compose())


__all__ = ["LOCAL_PRINCIPAL", "LocalKriyaClient", "local_client"]
