"""HealthService.capabilities (slice B2): the Kriya facts a client's capability check reports, extracted unchanged from
``kriya/kup/inspect.py`` (``capabilities``/``_cockpit_features``). The KUP adapter wraps them in its own protocol description
(versions, operations, limits, error codes, feature flags) and adds the trace reader's SQLite version.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from kriya.context.model import RequestContext
from kriya.core.attempt_evidence import model as evidence_model
from kriya.foundation.build_info import version_report


@dataclass(frozen=True)
class Capabilities:
    kriya_version: Optional[str]
    commit: Optional[str]
    build_provenance: Optional[str]
    content_authorized: bool               # Kriya's own SECURITY_AUTHORITY decision, never a host's
    evidence_capture_modes: Tuple[str, ...]
    evidence_kinds: Tuple[str, ...]        # sorted
    evidence_provenances: Tuple[str, ...]  # sorted
    diagnosis_evidence_classes: Tuple[str, ...]


class HealthService:
    def capabilities(self, ctx: RequestContext, cfg=None) -> Capabilities:
        """Build identity, whether recorded content may be shown (only an explicit ``evidence.attempt_recorder.content_visibility: on`` in
        the resolved configuration; no configuration, or anything else, is off) and the attempt-evidence vocabulary."""
        report = version_report()
        content_authorized = getattr(getattr(getattr(cfg, "evidence", None), "attempt_recorder", None), "content_visibility", "off") == "on"
        return Capabilities(kriya_version=report.get("version"), commit=report.get("commit"), build_provenance=report.get("build_provenance"),
                            content_authorized=content_authorized, evidence_capture_modes=tuple(evidence_model.CAPTURE_MODES),
                            evidence_kinds=tuple(sorted(evidence_model.KINDS)), evidence_provenances=tuple(sorted(evidence_model.PROVENANCES)),
                            diagnosis_evidence_classes=tuple(evidence_model.EVIDENCE_CLASSES))


__all__ = ["Capabilities", "HealthService"]
