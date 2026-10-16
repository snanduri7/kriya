"""APPLICATION (full modularization, W2; MODULE-CONTRACTS §15): the use-case services the client boundary calls.

Built ONCE from the existing operation bodies (B2: the read side extracted from the KUP operations; B3: the write/run side; B4:
``application.compose()`` and ``LocalKriyaClient``). A service orchestrates domain modules and owns no transport and no mechanism:
every method takes the ``RequestContext`` and the resolved configuration first, returns the data the adapter serializes, and refuses
with ``ApplicationRefusal(reason_code)`` (kriya/application/services.py). APPLICATION reads no environment, home directory or working
directory and opens no database or network connection (tests/architecture/test_direct_access_owners.py).

Names are resolved lazily (PEP 562): importing the package does not import every service's domain modules, so the KUP child pays only
for the operation it answers.
"""
from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "ApplicationRefusal": "kriya.application.services",
    "REFUSAL_CODES": "kriya.application.services",
    "TraceHistory": "kriya.application.services",
    "HistoryService": "kriya.application.history",
    "application_outcome": "kriya.application.history",
    "EvidenceService": "kriya.application.evidence",
    "RECORD_KINDS": "kriya.application.evidence",
    "SkillCatalogService": "kriya.application.catalog",
    "ToolCatalogService": "kriya.application.catalog",
    "ProjectService": "kriya.application.project",
    "ModelService": "kriya.application.model",
    "safe_endpoint": "kriya.application.model",
    "HealthService": "kriya.application.health",
    "Capabilities": "kriya.application.health",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'kriya.application' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
