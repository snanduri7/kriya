"""ConfigurationService (slice B3): the SEC-009 configuration-approval use case of the desktop, extracted unchanged from
``kriya/kup/authority_ops.py`` (``configuration.approval_status`` and ``configuration.approve``).

Nothing here decides authority. What needs approval, how it is digested, where the artifact lives and when it stops covering the
configuration are all ``kriya.config.authority`` / ``kriya.config.authority_approval``, exactly as for ``kriya authority inspect|approve``;
the service only orders those calls. It is ONE MORE CLIENT of that subsystem:

* ``approval_status`` is read-only and returns protected FIELD NAMES, their classification and source, and a review fingerprint. It
  returns NO configuration value: existing display redaction covers ``env`` and ``api_key`` only, and there is no authoritative
  per-field "safe to show" metadata, so none is shown.
* ``approve`` refuses (``APPROVAL_STALE``, nothing written) unless the fingerprint of the configuration it is given equals the one the
  person reviewed, and writes through the SAME function the CLI uses (``grant``: ``grant_local_approval``, passed by the caller).
* A trust file in effect or a store that would resolve inside the workspace is a typed ``APPROVAL_UNAVAILABLE``: the local store would
  not be the thing consulted, or must not be written.

The caller resolves the folder's configuration state (``resolve_config_state`` with the folder as the working directory: a cwd
mechanism APPLICATION does not own) and reads ``KRIYA_TRUST_FILE`` (an environment read, D3); ``approve`` runs while the caller holds
its working-directory lock, so the comparison and the write see the same configuration.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from kriya.application.services import APPROVAL_STALE, APPROVAL_UNAVAILABLE, ApplicationRefusal
from kriya.config.authority_approval import (
    ApprovalArtifactError,
    TrustPathInsideWorkspaceError,
    default_local_approval_path,
    is_approval_current_for,
    load_approval_artifact,
    resolve_with_approval,
    review_fingerprint,
    validate_trust_path_outside_workspace,
)
from kriya.context.model import RequestContext
from kriya.context.workspace_identity import workspace_identity

SCOPE = ("This approval applies only to the current operator-controlled settings of this folder. If any of them is added, removed or changed, "
         "Kriya asks for approval again.")
NO_VALUES_NOTE = "Only the names of the settings are shown. Their values are not sent to the application."


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


def _result(state, outcome: str, approval_digest: Optional[str]) -> Dict[str, Any]:
    return {"workspace": state.workspace_root, "identity": workspace_identity(state.workspace_root), "outcome": outcome, "approved_fields": [v.field_path for v in sorted(state.violations, key=lambda x: x.field_path)],
            "approval_digest": approval_digest, "scope": SCOPE}


class ConfigurationService:
    def approval_status(self, ctx: RequestContext, workspace: str, state, *, trust_file: Optional[str]) -> Dict[str, Any]:
        """The approval state of the folder ``workspace`` (canonical) whose configuration resolved to ``state``. Read-only."""
        data: Dict[str, Any] = {
            "workspace": workspace, "identity": workspace_identity(workspace), "status": "not_required", "reason": None, "required": False, "can_approve": False,
            "blocking_reason": None, "protected_fields": _fields(state.violations), "fingerprint": review_fingerprint(state.violations, state.config_dict),
            "scope": SCOPE, "values_shown": False, "note": NO_VALUES_NOTE,
        }
        if not state.violations:
            return data
        data["required"] = True
        blocked = _blocking(state, trust_file)
        if blocked is not None:
            data.update(status="unavailable", reason=blocked["reason"], blocking_reason=blocked["message"])
            return data
        covered, reason = _coverage(state, trust_file)
        data.update(status="approved" if covered else "required", reason=reason, can_approve=not covered and not trust_file)
        return data

    def approve(self, ctx: RequestContext, state, reviewed: str, *, trust_file: Optional[str],
                grant: Callable[..., Any]) -> Dict[str, Any]:
        """Record the approval of exactly the configuration ``reviewed`` names (its review fingerprint), through ``grant``. The caller holds
        its working-directory lock from resolving ``state`` until this returns."""
        current = review_fingerprint(state.violations, state.config_dict)
        if current != reviewed:
            raise ApplicationRefusal(APPROVAL_STALE, "The configuration changed after it was reviewed; nothing was approved. Review the current configuration before approving.",
                                     reason="configuration_changed")
        blocked = _blocking(state, trust_file)
        if blocked is not None:
            raise ApplicationRefusal(APPROVAL_UNAVAILABLE, blocked["message"], reason=blocked["reason"])
        covered, _why = _coverage(state, trust_file)
        if covered:
            return _result(state, "already_approved", None)
        try:
            artifact, _path = grant(state.violations, state.config_dict, state.workspace_root)
        except OSError as error:
            raise ApplicationRefusal(APPROVAL_UNAVAILABLE, f"The approval could not be saved: {error.strerror or type(error).__name__}", reason="store_unwritable") from error
        return _result(state, "approved", artifact.set_digest[:16])


__all__ = ["ConfigurationService", "NO_VALUES_NOTE", "SCOPE"]
