"""KUP ``configuration.approval_status`` and ``configuration.approve``: the desktop as ONE MORE CLIENT of the SEC-009 configuration-authority subsystem.

Transport only (full modularization B3, D3): argument validation and the envelope. The use case is ``client.configuration``
(``kriya.application.ConfigurationService``, through the ``KriyaClient``), which since D3 also resolves the folder's configuration
(``ConfigService``: the folder entered under CONTEXT's one working-directory lock) and reads the trust file (CONFIG's environment
reader); what follows describes it.

Nothing here decides authority. What needs approval, how it is digested, where the artifact lives and when it stops covering the configuration are all
``kriya.config.authority`` / ``kriya.config.authority_approval``, exactly as for ``kriya authority inspect|approve``. This module only (1) resolves the folder's
configuration the way a run there would (the process working directory decides discovery, as in ``execution.preflight``), (2) reports it, and (3) on an
explicit request, writes the approval through the SAME function the CLI uses (``grant_local_approval``, reached by the service through ``AuthorityPort`` since C1).

Boundaries, each pinned by a test:

* ``approval_status`` is read-only and returns protected FIELD NAMES, their classification and source, and a review fingerprint. It returns NO configuration
  value (a design choice kept after S2: the CLI shows values through the one redaction owner, kriya.foundation.redaction; the GUI shows none).
* ``approve`` takes ``{workspace, fingerprint}`` and nothing else: no fields, no values, no path, no digest to trust, no store location. It re-resolves the
  configuration fresh and refuses (``APPROVAL_STALE``, nothing written) unless the fingerprint of what it now sees equals the one the person reviewed.
* A trust file in effect (``KRIYA_TRUST_FILE``) or a store that would resolve inside the workspace is a typed ``APPROVAL_UNAVAILABLE``: the local store would not
  be the thing consulted, or must not be written.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from kriya.kup import policy
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.store import SnapshotError
from kriya.kup.workspace_ops import validated_workspace

FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def status(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:  # noqa: ARG001 - the operation table passes the configuration to every handler
    _requested, workspace = validated_workspace(request)
    data = client.configuration.approval_status(client.context(workspace_root=workspace, request_id=request_id), workspace)
    return envelope("configuration.approval_status", request_id, data=data, source=None, consistency=live_consistency())


def approve(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:  # noqa: ARG001 - the operation table passes the configuration to every handler
    reviewed = request.get("fingerprint")
    if not isinstance(reviewed, str) or not FINGERPRINT_RE.match(reviewed):
        raise SnapshotError(policy.INVALID_REQUEST, "fingerprint must be the review fingerprint Kriya gave (sha256:<64 hex>)", reason="invalid_fingerprint")
    _requested, workspace = validated_workspace(request)
    data = client.configuration.approve(client.context(workspace_root=workspace, request_id=request_id), reviewed)
    return envelope("configuration.approve", request_id, data=data, source=None, consistency=live_consistency())
