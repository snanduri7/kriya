"""Strict test doubles for the config and kernel (CLAUDE.md "Mandatory quality
bar" rule 3).

A bare ``MagicMock()`` config or kernel answers every attribute with a truthy
MagicMock, so any feature flag the code under test reads is silently ON.
PRD-020's terminal requirement verifier ran in two suites for exactly that
reason: ``kernel.config.autonomy.spec_compliance_enabled`` was a MagicMock.
These helpers give the code a real ``AppConfig`` (packaged defaults, the same
values production starts from) and a kernel that raises on any attribute
Kernel does not have. ``tests/test_strict_doubles.py`` rejects new bare
config/kernel/engine doubles."""
import os
import tempfile
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

from kriya.config.config import AppConfig
from kriya.core.events import EventSystem
from kriya.core.kernel import Kernel
from kriya.core.registry import ComponentRegistry


_DEFAULT_ROOTS: Dict[str, str] = {}


def _default_root() -> str:
    """One temp root per test (per process when no test is running), so every
    default ``strict_config()`` in a test is equal: a resume or reuse check
    that compares config fingerprints across two engines must not differ on
    a random path."""
    test_id = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" (", 1)[0]
    if test_id not in _DEFAULT_ROOTS:
        _DEFAULT_ROOTS[test_id] = tempfile.mkdtemp(prefix="kriya-strict-config-")
    return _DEFAULT_ROOTS[test_id]


def strict_config(**sections: Dict[str, Any]) -> AppConfig:
    """A validated ``AppConfig`` from packaged defaults plus overrides.

    ``strict_config(autonomy={"spec_compliance_enabled": True})``. Each value
    is validated by pydantic; an unknown section or field raises instead of
    being silently ignored. ``paths.skills``/``paths.memory`` default to a
    temp dir (the same one for every call within a test) so code that opens
    them never writes into the CWD."""
    fields = AppConfig.model_fields
    data: Dict[str, Any] = {}
    for section, values in sections.items():
        if section not in fields:
            raise KeyError(f"AppConfig has no section {section!r}")
        if isinstance(values, dict):
            model = fields[section].annotation
            known = getattr(model, "model_fields", None)
            if known is not None:
                unknown = sorted(set(values) - set(known))
                if unknown:
                    raise KeyError(f"{section} has no field(s) {unknown}")
        data[section] = values
    paths = dict(data.get("paths") or {})
    if "skills" not in paths or "memory" not in paths:
        root = _default_root()
        paths.setdefault("skills", f"{root}/skills")
        paths.setdefault("memory", f"{root}/memory")
    data["paths"] = paths
    return AppConfig(**data)


def strict_kernel(config: Optional[AppConfig] = None, *, registry: Any = None) -> MagicMock:
    """A ``Kernel``-spec'd double with a real config.

    Reading an attribute Kernel does not define raises AttributeError. The
    instance attributes ``Kernel.__init__`` sets are set explicitly: a real
    ``config`` (``strict_config()`` unless given), a spec'd ``registry``
    (or the one passed), a spec'd ``events`` and a ``mcp`` double."""
    kernel = MagicMock(spec=Kernel)
    kernel.config = config if config is not None else strict_config()
    kernel.registry = registry if registry is not None else MagicMock(spec=ComponentRegistry)
    kernel.events = MagicMock(spec=EventSystem)
    kernel.mcp = MagicMock()
    return kernel


def strict_engine(config: Optional[AppConfig] = None) -> MagicMock:
    """A WorkflowEngine double whose ``kernel`` is ``strict_kernel(config)``.

    The engine itself stays a plain MagicMock (tests assign the methods they
    drive); what matters is that config reached through ``engine.kernel`` is
    real."""
    engine = MagicMock()
    engine.kernel = strict_kernel(config)
    return engine
