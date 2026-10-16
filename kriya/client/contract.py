"""The ``KriyaClient`` contract (slice B4; MODULE-CONTRACTS §16): the semantic boundary every Kriya front end talks to.

``KriyaClient`` is a Protocol: the desktop's KUP adapter and (from O2) the CLI call it; ``LocalKriyaClient`` (kriya/client/local.py) is
the only implementation now, in-process over ``kriya.application`` services; a future ``RemoteKriyaClient`` implements the SAME Protocol
over a transport. Every operation takes a ``RequestContext`` first (``client.context(...)`` builds it), which carries the principal, the
workspace and the request identity: nothing behind the contract may infer them from process-global state.

Each service port below is typed parameter for parameter with the application service that implements it
(tests/contracts/test_kriya_client_contract.py compares the signatures). Refusals are ``ApplicationRefusal(reason_code)`` with codes from
``kriya.application.services.REFUSAL_CODES``, the KUP error vocabulary one to one; the launch decision keeps AUTHORITY's own
``ApprovalRefused`` (code, message) unchanged.

TEMPORARY local-only parameters. Some ports still take a mechanism or a live object that cannot cross a transport, because the domain
module that will own it does not exist yet. They are listed in ``LOCAL_ONLY_PARAMETERS`` with the slice that removes each (the contract
suite fails on any callable/object parameter that is not listed), so a RemoteKriyaClient's gap is explicit, not hidden:
the folder's configuration resolved with the folder as working directory (``load_in``, ``state``: D3 ``ConfigService.resolve(ctx)``),
the trust-file path read from the environment (``trust_file``: D3), the approval writer (``grant``: C1 ``AuthorityPort.config_authority``),
the toolchain probe (``tool_visibility``: L1 ``ExecutionPort``), the model-endpoint probe (``probe``: I2 ``InferenceRuntimePort.health``),
the per-request trace snapshot (``trace``: D2 ``HistoryStore``), the run composition (``deps``/``artifacts`` of ``runs.start``: O2,
which moves ``kriya generate`` onto the client) and the resolved configuration object itself (``cfg``: D3 ``ResolvedConfiguration``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, Protocol, Tuple, runtime_checkable

if TYPE_CHECKING:
    from kriya.application import (
        Capabilities,
        EvidenceAnswer,
        EvidenceLocation,
        LoadIn,
        ProbeEndpoint,
        RecordsPage,
        ReviewDecisionRequest,
        RunArtifacts,
        RunHandle,
        RunServiceDeps,
        ToolVisibility,
    )
    from kriya.application.services import TraceHistory
    from kriya.config import AppConfig
    from kriya.config.config import ConfigResolutionState
    from kriya.context import RequestContext


class ProjectsPort(Protocol):
    def status(self, ctx: "RequestContext", workspace: str) -> Dict[str, Any]: ...
    def active_run(self, ctx: "RequestContext", workspace: str) -> Optional[str]: ...
    def describe(self, ctx: "RequestContext", cfg: "AppConfig", requested: str, workspace: str) -> Dict[str, Any]: ...
    def create(self, ctx: "RequestContext", parent: str, name: Any) -> Dict[str, Any]: ...
    def initialize(self, ctx: "RequestContext", workspace: str) -> Dict[str, Any]: ...


class RunsPort(Protocol):
    def preflight(self, ctx: "RequestContext", cfg: "AppConfig", workspace: str, *, load_in: "LoadIn",
                  tool_visibility: "ToolVisibility") -> Dict[str, Any]: ...
    def build_preflight(self, ctx: "RequestContext", workspace: str, cockpit_state_directory: Optional[str], *, load_in: "LoadIn",
                        tool_visibility: "ToolVisibility") -> Dict[str, Any]: ...
    def status(self, ctx: "RequestContext", cfg: "AppConfig", workspace: str, launch_id: str) -> Dict[str, Any]: ...
    def start(self, ctx: "RequestContext", cfg: "AppConfig", artifacts: "RunArtifacts", deps: "RunServiceDeps") -> "RunHandle": ...
    def mark_in_progress(self, ctx: "RequestContext", cfg: "AppConfig", run_id: Optional[str], goal: str) -> None: ...


class ApprovalsPort(Protocol):
    def decide(self, ctx: "RequestContext", request: "ReviewDecisionRequest") -> Dict[str, Any]: ...


class ReviewsPort(Protocol):
    def change_review(self, ctx: "RequestContext", workspace: str, launch_id: str, review_id: str) -> Dict[str, Any]: ...
    def runtime_command_review(self, ctx: "RequestContext", workspace: str, launch_id: str, review_id: str) -> Dict[str, Any]: ...


class ConfigurationPort(Protocol):
    def approval_status(self, ctx: "RequestContext", workspace: str, state: "ConfigResolutionState", *,
                        trust_file: Optional[str]) -> Dict[str, Any]: ...
    def approve(self, ctx: "RequestContext", state: "ConfigResolutionState", reviewed: str, *, trust_file: Optional[str],
                grant: Any) -> Dict[str, Any]: ...


class ModelsPort(Protocol):
    def skills(self, ctx: "RequestContext", cfg: "AppConfig", workspace: str) -> Dict[str, Any]: ...
    def context(self, ctx: "RequestContext", cfg: "AppConfig", skills: Dict[str, Any]) -> Dict[str, Any]: ...
    def probe(self, ctx: "RequestContext", cfg: "AppConfig", *, probe: "ProbeEndpoint", deadline_seconds: float,
              max_endpoints: int) -> Dict[str, Any]: ...


class SkillsPort(Protocol):
    def list(self, ctx: "RequestContext", cfg: "AppConfig", workspace: str) -> Dict[str, Any]: ...
    def describe(self, ctx: "RequestContext", cfg: "AppConfig", workspace: str, skill_id: str) -> Dict[str, Any]: ...


class ToolsPort(Protocol):
    def list(self, ctx: "RequestContext", cfg: "AppConfig", workspace: str) -> Dict[str, Any]: ...
    def describe(self, ctx: "RequestContext", cfg: "AppConfig", workspace: str, tool_id: str) -> Dict[str, Any]: ...
    def approval_state(self, ctx: "RequestContext", cfg: "AppConfig", workspace: str, tool_id: str) -> Dict[str, Any]: ...


class EvidencePort(Protocol):
    def location(self, ctx: "RequestContext", cfg: "AppConfig") -> "EvidenceLocation": ...
    def manifest(self, ctx: "RequestContext", cfg: "AppConfig", asked: str, *, from_trace: bool,
                 trace: Optional["TraceHistory"]) -> Dict[str, Any]: ...
    def verify(self, ctx: "RequestContext", cfg: "AppConfig", run_id: Any) -> "EvidenceAnswer": ...
    def records(self, ctx: "RequestContext", cfg: "AppConfig", run_id: str, *, filters: Dict[str, Any], limit: int,
                cursor_head: Optional[str], after_seq: int, max_page_bytes: int) -> "RecordsPage": ...
    def content(self, ctx: "RequestContext", cfg: "AppConfig", run_id: str, *, seq: int, part: str, record_kind: str, content_class: str,
                max_bytes: int) -> "EvidenceAnswer": ...
    def store_summary(self, ctx: "RequestContext", cfg: "AppConfig", run_id: str) -> Dict[str, Any]: ...


class HistoryPort(Protocol):
    def list(self, ctx: "RequestContext", cfg: "AppConfig", trace: "TraceHistory", *, limit: Optional[int],
             cursor: Optional[str]) -> Dict[str, Any]: ...
    def list_in_workspace(self, ctx: "RequestContext", cfg: "AppConfig", trace: "TraceHistory", *, limit: int, cursor: Optional[str],
                          workspace: str) -> Dict[str, Any]: ...
    def detail(self, ctx: "RequestContext", cfg: "AppConfig", trace: "TraceHistory", run_id: str) -> Dict[str, Any]: ...
    def prompt(self, ctx: "RequestContext", cfg: "AppConfig", trace: "TraceHistory", run_id: str) -> Dict[str, Any]: ...


class HealthPort(Protocol):
    def capabilities(self, ctx: "RequestContext", cfg: Optional["AppConfig"] = None) -> "Capabilities": ...


PORTS: Mapping[str, type] = {
    "projects": ProjectsPort, "runs": RunsPort, "approvals": ApprovalsPort, "reviews": ReviewsPort, "configuration": ConfigurationPort,
    "models": ModelsPort, "skills": SkillsPort, "tools": ToolsPort, "evidence": EvidencePort, "history": HistoryPort, "health": HealthPort,
}

# (port, method, parameter) -> the slice that replaces it with a transportable value. Closed: the contract suite fails on an unlisted
# mechanism/object parameter and on a listed one that no longer exists.
LOCAL_ONLY_PARAMETERS: Mapping[Tuple[str, str, str], str] = {
    **{(port, method, "cfg"): "D3" for port, methods in (
        ("projects", ("describe",)), ("runs", ("preflight", "status", "start", "mark_in_progress")),
        ("models", ("skills", "context", "probe")), ("skills", ("list", "describe")), ("tools", ("list", "describe", "approval_state")),
        ("evidence", ("location", "manifest", "verify", "records", "content", "store_summary")),
        ("history", ("list", "list_in_workspace", "detail", "prompt")), ("health", ("capabilities",))) for method in methods},
    ("runs", "preflight", "load_in"): "D3", ("runs", "build_preflight", "load_in"): "D3",
    ("runs", "preflight", "tool_visibility"): "L1", ("runs", "build_preflight", "tool_visibility"): "L1",
    ("runs", "start", "artifacts"): "O2", ("runs", "start", "deps"): "O2",
    ("configuration", "approval_status", "state"): "D3", ("configuration", "approve", "state"): "D3",
    ("configuration", "approval_status", "trust_file"): "D3", ("configuration", "approve", "trust_file"): "D3",
    ("configuration", "approve", "grant"): "C1",
    ("models", "probe", "probe"): "I2",
    ("evidence", "manifest", "trace"): "D2",
    **{("history", method, "trace"): "D2" for method in ("list", "list_in_workspace", "detail", "prompt")},
}


@runtime_checkable
class KriyaClient(Protocol):
    """The services of Kriya as one client (MODULE-CONTRACTS §16)."""

    projects: ProjectsPort
    runs: RunsPort
    approvals: ApprovalsPort
    reviews: ReviewsPort
    configuration: ConfigurationPort
    models: ModelsPort
    skills: SkillsPort
    tools: ToolsPort
    evidence: EvidencePort
    history: HistoryPort
    health: HealthPort

    def context(self, *, workspace_root: Optional[str], request_id: Optional[str] = None) -> "RequestContext": ...


__all__ = ["ApprovalsPort", "ConfigurationPort", "EvidencePort", "HealthPort", "HistoryPort", "KriyaClient", "LOCAL_ONLY_PARAMETERS",
           "ModelsPort", "PORTS", "ProjectsPort", "ReviewsPort", "RunsPort", "SkillsPort", "ToolsPort"]
