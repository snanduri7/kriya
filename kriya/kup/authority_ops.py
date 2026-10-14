"""KUP ``configuration.approval_status`` and ``configuration.approve``: the desktop as ONE MORE CLIENT of the SEC-009 configuration-authority subsystem.

Nothing here decides authority. What needs approval, how it is digested, where the artifact lives and when it stops covering the configuration are all
``kriya.config.authority`` / ``kriya.config.authority_approval``, exactly as for ``kriya authority inspect|approve``. This module only (1) resolves the folder's
configuration the way a run there would (the process working directory decides discovery, as in ``execution.preflight``), (2) reports it, and (3) on an
explicit request, writes the approval through the SAME function the CLI uses (``grant_local_approval``).

Boundaries, each pinned by a test:

* ``approval_status`` is read-only and returns protected FIELD NAMES, their classification and source, and a review fingerprint. It returns NO configuration
  value: existing display redaction covers ``env`` and ``api_key`` only, and there is no authoritative per-field "safe to show" metadata, so none is shown.
* ``approve`` takes ``{workspace, fingerprint}`` and nothing else: no fields, no values, no path, no digest to trust, no store location. It re-resolves the
  configuration fresh and refuses (``APPROVAL_STALE``, nothing written) unless the fingerprint of what it now sees equals the one the person reviewed.
* A trust file in effect (``KRIYA_TRUST_FILE``) or a store that would resolve inside the workspace is a typed ``APPROVAL_UNAVAILABLE``: the local store would not
  be the thing consulted, or must not be written.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from kriya.config.authority_approval import (
    ENV_TRUST_FILE,
    ApprovalArtifactError,
    TrustPathInsideWorkspaceError,
    default_local_approval_path,
    grant_local_approval,
    is_approval_current_for,
    load_approval_artifact,
    resolve_with_approval,
    review_fingerprint,
    validate_trust_path_outside_workspace,
)
from kriya.control.workspace_identity import workspace_identity
from kriya.kup import policy
from kriya.kup.execution import _CWD_LOCK
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.store import SnapshotError
from kriya.kup.workspace_ops import validated_workspace

FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SCOPE = ("This approval applies only to the current operator-controlled settings of this folder. If any of them is added, removed or changed, "
         "Kriya asks for approval again.")
NO_VALUES_NOTE = "Only the names of the settings are shown. Their values are not sent to the application."


def _resolve_locked(workspace: str):
    """The folder's configuration state, resolved with ``workspace`` as the working directory (as a terminal run there). The caller holds ``_CWD_LOCK``.
    Typed CONFIG_LOAD_FAILED on failure."""
    from kriya.config.config import resolve_config_state

    previous = os.getcwd()
    os.chdir(workspace)
    try:
        return resolve_config_state()
    except Exception as error:  # noqa: BLE001 - a malformed or unreadable kriya.yaml is a typed refusal, never a traceback (as execution.preflight)
        raise SnapshotError(policy.CONFIG_LOAD_FAILED, f"the configuration cannot be read: {type(error).__name__}: {str(error)[:300]}") from error
    finally:
        os.chdir(previous)


def _fields(violations) -> List[Dict[str, str]]:
    return [{"field": v.field_path, "classification": v.classification.value, "source": v.source.value}
            for v in sorted(violations, key=lambda x: x.field_path)]


def _blocking(state, trust_file: Optional[str]) -> Optional[Dict[str, str]]:
    """Why an approval from this application would not be the thing Kriya consults (or may not be written), or None."""
    try:
        if trust_file:
            validate_trust_path_outside_workspace(trust_file, state.workspace_root)
            return {"reason": "trust_file_in_effect",
                    "message": "A trust file is in effect for Kriya (KRIYA_TRUST_FILE), so approvals given here would not be consulted."}
        default_local_approval_path(state.workspace_root)
    except TrustPathInsideWorkspaceError:
        return {"reason": "trust_file_inside_workspace" if trust_file else "store_inside_workspace",
                "message": "The approval store is configured inside this folder, which Kriya refuses; it must live outside the folder."}
    return None


def _coverage(state, trust_file: Optional[str]):
    """(covered, reason): whether a current approval covers every violation (the verdict ``load_config`` reaches), and why not."""
    remaining = resolve_with_approval(state.violations, state.config_dict, state.workspace_root, trust_file)
    if not remaining:
        return True, None
    if trust_file:
        return False, "no_approval"
    try:
        artifact = load_approval_artifact(default_local_approval_path(state.workspace_root))
    except ApprovalArtifactError:
        return False, "store_unreadable"
    if artifact is None:
        return False, "no_approval"
    return (False, "stale_approval") if not is_approval_current_for(artifact, state.violations, state.config_dict, state.workspace_root) else (False, "no_approval")


def status(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:  # noqa: ARG001 - the operation table passes the configuration to every handler
    _requested, workspace = validated_workspace(request)
    with _CWD_LOCK:
        state = _resolve_locked(workspace)
    trust_file = os.environ.get(ENV_TRUST_FILE) or None
    data: Dict[str, Any] = {
        "workspace": workspace, "identity": workspace_identity(workspace), "status": "not_required", "reason": None, "required": False, "can_approve": False,
        "blocking_reason": None, "protected_fields": _fields(state.violations), "fingerprint": review_fingerprint(state.violations, state.config_dict),
        "scope": SCOPE, "values_shown": False, "note": NO_VALUES_NOTE,
    }
    if not state.violations:
        return envelope("configuration.approval_status", request_id, data=data, source=None, consistency=live_consistency())
    data["required"] = True
    blocked = _blocking(state, trust_file)
    if blocked is not None:
        data.update(status="unavailable", reason=blocked["reason"], blocking_reason=blocked["message"])
        return envelope("configuration.approval_status", request_id, data=data, source=None, consistency=live_consistency())
    covered, reason = _coverage(state, trust_file)
    data.update(status="approved" if covered else "required", reason=reason, can_approve=not covered and not trust_file)
    return envelope("configuration.approval_status", request_id, data=data, source=None, consistency=live_consistency())


def approve(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:  # noqa: ARG001 - the operation table passes the configuration to every handler
    reviewed = request.get("fingerprint")
    if not isinstance(reviewed, str) or not FINGERPRINT_RE.match(reviewed):
        raise SnapshotError(policy.INVALID_REQUEST, "fingerprint must be the review fingerprint Kriya gave (sha256:<64 hex>)", reason="invalid_fingerprint")
    _requested, workspace = validated_workspace(request)
    trust_file = os.environ.get(ENV_TRUST_FILE) or None
    with _CWD_LOCK:  # resolve, compare and write under one hold: the working directory cannot change between the comparison and the write
        state = _resolve_locked(workspace)
        current = review_fingerprint(state.violations, state.config_dict)
        if current != reviewed:
            raise SnapshotError(policy.APPROVAL_STALE, "The configuration changed after it was reviewed; nothing was approved. Review the current configuration before approving.",
                                reason="configuration_changed")
        blocked = _blocking(state, trust_file)
        if blocked is not None:
            raise SnapshotError(policy.APPROVAL_UNAVAILABLE, blocked["message"], reason=blocked["reason"])
        covered, _why = _coverage(state, trust_file)
        if covered:
            return envelope("configuration.approve", request_id, data=_result(state, "already_approved", None), source=None, consistency=live_consistency())
        try:
            artifact, _path = grant_local_approval(state.violations, state.config_dict, state.workspace_root)
        except OSError as error:
            raise SnapshotError(policy.APPROVAL_UNAVAILABLE, f"The approval could not be saved: {error.strerror or type(error).__name__}", reason="store_unwritable") from error
    return envelope("configuration.approve", request_id, data=_result(state, "approved", artifact.set_digest[:16]), source=None, consistency=live_consistency())


def _result(state, outcome: str, approval_digest: Optional[str]) -> Dict[str, Any]:
    return {"workspace": state.workspace_root, "identity": workspace_identity(state.workspace_root), "outcome": outcome, "approved_fields": [v.field_path for v in sorted(state.violations, key=lambda x: x.field_path)],
            "approval_digest": approval_digest, "scope": SCOPE}
