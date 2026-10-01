"""Test-only static-analysis doubles (PRD-031A). kriya/ must never import
or register anything from here (tests/test_prd031a_static_analysis.py).

``DISABLED_STATIC_ANALYSIS``: the commit guard under the packaged default
configuration (static analysis disabled) - what every commit-seam test that
is not about static analysis passes to ``commit_terminal_candidate``.

``FakeProvider``: a real port implementation that scans the snapshot files
it is given. A line containing ``BAD_<SEVERITY>`` (e.g. ``BAD_HIGH``) is a
finding of rule ``fake:bad-<severity>`` on that line. In cross-file mode a
line ``CALLS_TAINTED`` is a finding when any scanned file contains
``TAINT_SOURCE`` - a candidate change in one file causing a finding in an
unchanged one. Every failure mode is a knob.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

from kriya.config.config import AppConfig
from kriya.static_analysis.model import (
    Finding,
    LanguageSupport,
    PrerequisiteResult,
    ProviderCapability,
    ProviderIdentity,
    ProviderProbe,
    RulePackIdentity,
    ScanResult,
    ScanScope,
    ScanStatus,
    Severity,
    canonical_digest,
)
from kriya.static_analysis.port import ExecutionContext, ScanRequest
from kriya.static_analysis.registry import _REGISTRY, register_provider
from kriya.static_analysis.service import commit_guard

DISABLED_STATIC_ANALYSIS = commit_guard(AppConfig(), None)

FAKE = "fake"
FAKE_VERSION = "9.9.9"


@dataclass
class FakeKnobs:
    languages: Dict[str, LanguageSupport] = field(default_factory=lambda: {
        "java": LanguageSupport("ga", 3), "python": LanguageSupport("ga", 3),
    })
    cross_file: bool = False
    minimum_scope: ScanScope = ScanScope.CHANGED_FILES
    supported_scopes: Tuple[ScanScope, ...] = (ScanScope.CHANGED_FILES, ScanScope.MODULE, ScanScope.REPOSITORY)
    network_requirement: str = "none"
    source_upload: bool = False
    prerequisites: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    unmet_prerequisites: Tuple[str, ...] = ()
    control_files: Tuple[str, ...] = (".fakeignore",)
    probe_failure: Optional[str] = None
    # side ("pre"/"post") -> forced status
    status: Dict[str, ScanStatus] = field(default_factory=dict)
    reported_version: Dict[str, Optional[str]] = field(default_factory=dict)
    # paths the scanner silently does not confirm (never analyzed)
    unconfirmed: Tuple[str, ...] = ()
    raise_in_scan: bool = False
    runtime_marker: str = "v1"
    rule_pack_digest: str = "pack-digest-1"


@dataclass
class FakeProvider:
    knobs: FakeKnobs
    context: ExecutionContext
    scans: List[ScanRequest] = field(default_factory=list)
    # scan_id -> {relpath: text} of every file present in the snapshot root
    trees: Dict[str, Dict[str, str]] = field(default_factory=dict)
    probes: int = 0
    name: str = FAKE

    def probe(self) -> ProviderProbe:
        self.probes += 1
        if self.knobs.probe_failure:
            return ProviderProbe(identity=None, capability=None, reason_code=self.knobs.probe_failure, detail="forced")
        identity = ProviderIdentity(
            provider=FAKE, version=FAKE_VERSION, edition="test",
            executable_digest="exe-" + self.knobs.runtime_marker, execution_location="local_process",
            network_enforced=False,
            rule_packs=(RulePackIdentity("fake-pack", self.knobs.rule_pack_digest, 3, tuple(self.knobs.languages)),),
            effective_options_digest="opts", severity_map_version=1,
        )
        capability = ProviderCapability(
            languages=dict(self.knobs.languages),
            analysis_scope="cross_file" if self.knobs.cross_file else "file_local",
            supported_scopes=frozenset(self.knobs.supported_scopes), minimum_scope=self.knobs.minimum_scope,
            network_requirement=self.knobs.network_requirement, source_upload=self.knobs.source_upload,
            prerequisites=dict(self.knobs.prerequisites), control_files=self.knobs.control_files,
            limitations=("fake provider",),
        )
        return ProviderProbe(identity=identity, capability=capability)

    def check_prerequisites(
        self, capability: ProviderCapability, languages: Sequence[str], workspace: str,
    ) -> Sequence[PrerequisiteResult]:
        return tuple(
            PrerequisiteResult(lang, prerequisite, prerequisite not in self.knobs.unmet_prerequisites)
            for lang in languages for prerequisite in capability.prerequisites.get(lang, ())
        )

    def build_graph_roots(self, workspace: str, changed_paths: Sequence[str]) -> Sequence[str]:
        return ()

    def runtime_fingerprint(self) -> str:
        return canonical_digest(["fake", self.knobs.runtime_marker, self.knobs.rule_pack_digest])

    def scan(self, request: ScanRequest) -> ScanResult:
        self.scans.append(request)
        if self.knobs.raise_in_scan:
            raise RuntimeError("fake scanner bug")
        forced = self.knobs.status.get(request.scan_id)
        tree: Dict[str, str] = {}
        for directory, _dirs, files in os.walk(request.root):
            for name in files:
                path = os.path.join(directory, name)
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    tree[os.path.relpath(path, request.root).replace(os.sep, "/")] = handle.read()
        self.trees[request.scan_id] = tree
        contents = {}
        for target in request.targets:
            with open(os.path.join(request.root, target), "r", encoding="utf-8") as handle:
                contents[target] = handle.read().splitlines()
        tainted = self.knobs.cross_file and any("TAINT_SOURCE" in line for lines in contents.values() for line in lines)
        findings: List[Finding] = []
        for target, lines in sorted(contents.items()):
            for number, line in enumerate(lines, start=1):
                for severity in Severity:
                    if f"BAD_{severity.name}" in line:
                        findings.append(Finding(
                            provider=FAKE, rule_id=f"fake:bad-{severity.value}", severity=severity, path=target,
                            start_line=number, end_line=number, message=f"SCANNER-TEXT {line.strip()}",
                            raw_index=len(findings),
                        ))
                if tainted and "CALLS_TAINTED" in line:
                    findings.append(Finding(
                        provider=FAKE, rule_id="fake:tainted-call", severity=Severity.HIGH, path=target,
                        start_line=number, end_line=number, message="SCANNER-TEXT tainted", raw_index=len(findings),
                    ))
        analyzed = frozenset(t for t in request.targets if t not in self.knobs.unconfirmed)
        status = forced or (ScanStatus.COMPLETE if analyzed == set(request.targets) else ScanStatus.INCOMPLETE)
        return ScanResult(
            status=status, findings=tuple(findings), analyzed=analyzed,
            reported_version=self.knobs.reported_version.get(request.scan_id, FAKE_VERSION),
            raw_output='{"fake": true}', raw_sha256="raw", reason_code=None,
        )


class FakeRegistration:
    """Registers the fake provider for one test; every created instance is kept."""

    def __init__(self, knobs: Optional[FakeKnobs] = None) -> None:
        self.knobs = knobs or FakeKnobs()
        self.instances: List[FakeProvider] = []

    def __enter__(self) -> "FakeRegistration":
        def factory(settings: dict, context: ExecutionContext) -> FakeProvider:
            del settings
            provider = FakeProvider(replace(self.knobs), context)
            self.instances.append(provider)
            return provider
        register_provider(FAKE, factory)
        return self

    def __exit__(self, *exc: object) -> None:
        _REGISTRY.pop(FAKE, None)

    @property
    def scans(self) -> List[ScanRequest]:
        return [scan for instance in self.instances for scan in instance.scans]
