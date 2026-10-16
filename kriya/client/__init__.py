"""CLIENT (full modularization, slice B4; MODULE-CONTRACTS §16): the semantic boundary between Kriya's front ends and its services.

``KriyaClient`` (kriya/client/contract.py) is the Protocol; ``LocalKriyaClient`` (kriya/client/local.py) the in-process implementation.
The adapters see the application only through this package: besides the client, it carries the contract vocabulary an adapter needs to
translate a request (the typed refusal ``ApplicationRefusal`` it maps 1:1 onto its error envelope, the closed evidence ``RECORD_KINDS`` a
records filter may name, and ``safe_endpoint``, the one rule for showing an endpoint without its credential).

Names are resolved lazily (PEP 562), like ``kriya.application``: the KUP child pays only for the operation it answers.
"""
from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "KriyaClient": "kriya.client.contract",
    "LOCAL_ONLY_PARAMETERS": "kriya.client.contract",
    "PORTS": "kriya.client.contract",
    "LocalKriyaClient": "kriya.client.local",
    "local_client": "kriya.client.local",
    "ApplicationRefusal": "kriya.application.services",
    "REFUSAL_CODES": "kriya.application.services",
    "RECORD_KINDS": "kriya.application",
    "safe_endpoint": "kriya.application",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'kriya.client' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
