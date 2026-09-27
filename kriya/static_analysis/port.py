"""The provider port (PRD-031A §5).

A provider reports identity and capability, checks its own prerequisites,
optionally resolves a BUILD_GRAPH scope, and scans a Kriya-owned snapshot.
It never decides policy, never chooses its own targets and never raises for
a scanner failure: a failure is a ScanResult status.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence, Tuple

from kriya.static_analysis.model import (
    PrerequisiteResult,
    ProviderCapability,
    ProviderProbe,
    ScanResult,
)


@dataclass(frozen=True)
class ScanRequest:
    # A Kriya-owned snapshot root; every target exists under it.
    root: str
    targets: Tuple[str, ...]
    timeout_seconds: int
    # Kriya-owned scratch directory (HOME, provider state); outside the
    # snapshot, removed with it.
    scratch_dir: str
    scan_id: str
    # Kriya already removed every target above this size (the source of
    # truth); a provider may pass it to its own size flag as defense in
    # depth only.
    max_target_bytes: int


@dataclass(frozen=True)
class ExecutionContext:
    """What the service tells a provider about where it must run.

    ``contained`` is True when autonomy.contained_execution_required: the
    provider must run through ``containment_backend`` with no network, and
    must refuse (never fall back to the host) if it cannot.
    """

    contained: bool
    containment_backend: Optional[object]
    workspace_root: str


class StaticAnalysisPort(Protocol):
    name: str

    def probe(self) -> ProviderProbe: ...

    def check_prerequisites(
        self, capability: ProviderCapability, languages: Sequence[str], workspace: str,
    ) -> Sequence[PrerequisiteResult]: ...

    def build_graph_roots(self, workspace: str, changed_paths: Sequence[str]) -> Sequence[str]: ...

    def scan(self, request: ScanRequest) -> ScanResult: ...

    def runtime_fingerprint(self) -> str:
        """A cheap digest of what would run, WITHOUT running the tool:
        the executable (host) or pinned image reference (container), every
        rule pack's content digest, and the effective options. The commit
        guard recomputes it to refuse evidence whose runtime, rule packs or
        settings changed after the scan. Raises on an unreadable executable
        or rule pack (the guard treats that as stale)."""
        ...


# (provider settings dict, execution context) -> port
ProviderFactory = Callable[[dict, ExecutionContext], StaticAnalysisPort]
