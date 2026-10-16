"""``application.compose()`` (slice B4; MODULE-CONTRACTS §15): the application services of one process, composed ONCE.

``ApplicationServices`` holds the eleven services the client boundary exposes. Each service is constructed on first use (and then kept),
so a one-shot command such as ``kriya traces --json`` still imports only the domain modules of the operation it answers - the lazy-import
property the KUP read path has had since B2 (``kriya/application/__init__.py``). The services hold no per-request state: every method
takes the ``RequestContext`` and the resolved configuration of that request, so one composition serves every request of the process and
SECURITY_AUTHORITY is never cached here (``kriya/kup/serve.py`` still reloads the configuration per request).

Since D2 the history service owns STATE's ``HistoryStore`` (built with the service) and its methods take a published ``Snapshot`` value;
the per-call ``TraceHistory`` reader parameter is gone.
"""
from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # annotations only: each service module is imported when the service is first used
    from kriya.application.approval import ApprovalService
    from kriya.application.catalog import SkillCatalogService, ToolCatalogService
    from kriya.application.configuration import ConfigurationService
    from kriya.application.evidence import EvidenceService
    from kriya.application.health import HealthService
    from kriya.application.history import HistoryService
    from kriya.application.model import ModelService
    from kriya.application.project import ProjectService
    from kriya.application.review import ReviewService
    from kriya.application.run import RunService
    from kriya.config.service import ConfigService

SERVICE_NAMES = ("projects", "runs", "approvals", "reviews", "configuration", "models", "skills", "tools", "evidence", "history", "health")


class ApplicationServices:
    """The use-case services of one process (MODULE-CONTRACTS §15 ``ApplicationServices``), each built once on first use. ``config`` is
    the ``ConfigService`` the services that resolve a folder's configuration use (D3; default: the process environment's)."""

    def __init__(self, config: "Optional[ConfigService]" = None) -> None:
        self._config = config

    @cached_property
    def projects(self) -> "ProjectService":
        from kriya.application.project import ProjectService
        return ProjectService()

    @cached_property
    def runs(self) -> "RunService":
        from kriya.application.run import RunService
        return RunService(self._config)

    @cached_property
    def approvals(self) -> "ApprovalService":
        from kriya.application.approval import ApprovalService
        return ApprovalService()

    @cached_property
    def reviews(self) -> "ReviewService":
        from kriya.application.review import ReviewService
        return ReviewService()

    @cached_property
    def configuration(self) -> "ConfigurationService":
        from kriya.application.configuration import ConfigurationService
        return ConfigurationService(config=self._config)

    @cached_property
    def models(self) -> "ModelService":
        from kriya.application.model import ModelService
        return ModelService()

    @cached_property
    def skills(self) -> "SkillCatalogService":
        from kriya.application.catalog import SkillCatalogService
        return SkillCatalogService()

    @cached_property
    def tools(self) -> "ToolCatalogService":
        from kriya.application.catalog import ToolCatalogService
        return ToolCatalogService()

    @cached_property
    def evidence(self) -> "EvidenceService":
        from kriya.application.evidence import EvidenceService
        return EvidenceService()

    @cached_property
    def history(self) -> "HistoryService":
        from kriya.application.history import HistoryService
        return HistoryService()

    @cached_property
    def health(self) -> "HealthService":
        from kriya.application.health import HealthService
        return HealthService()


def compose(config: "Optional[ConfigService]" = None) -> ApplicationServices:
    """The services of this process. Nothing is resolved here: no configuration, environment, home or working directory is read."""
    return ApplicationServices(config)


__all__ = ["ApplicationServices", "SERVICE_NAMES", "compose"]
