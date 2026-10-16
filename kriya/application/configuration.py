"""ConfigurationService (slice B3): the SEC-009 configuration-approval use case of the desktop, extracted unchanged from
``kriya/kup/authority_ops.py`` (``configuration.approval_status`` and ``configuration.approve``).

Nothing here decides authority. What needs approval, how it is digested, where the artifact lives and when it stops covering the
configuration are all ``kriya.config.authority`` / ``kriya.config.authority_approval``, exactly as for ``kriya authority inspect|approve``;
the service only orders those calls. It is ONE MORE CLIENT of that subsystem:

* ``approval_status`` is read-only and returns protected FIELD NAMES, their classification and source, and a review fingerprint. It
  returns NO configuration value: existing display redaction covers ``env`` and ``api_key`` only, and there is no authoritative
  per-field "safe to show" metadata, so none is shown.
* ``approve`` refuses (``APPROVAL_STALE``, nothing written) unless the fingerprint of the configuration it is given equals the one the
  person reviewed, and writes through the SAME function the CLI uses (``grant_local_approval``, reached through
  ``AuthorityPort.grant_config_approval`` since C1; before C1 the KUP caller passed it in as ``grant``).
* A trust file in effect or a store that would resolve inside the workspace is a typed ``APPROVAL_UNAVAILABLE``: the local store would
  not be the thing consulted, or must not be written.

Since D3 the service resolves the folder's configuration itself through ``ConfigService`` (CONFIG: the folder entered under CONTEXT's one
working-directory lock, exactly as the KUP adapter did before) and reads the trust file through CONFIG's environment reader; ``approve``
resolves, compares and writes under ONE hold of that lock, so the comparison and the write see the same configuration. A configuration
that cannot be read is the typed ``CONFIG_LOAD_FAILED`` refusal the adapter gave before.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from kriya.application.services import APPROVAL_STALE, APPROVAL_UNAVAILABLE, CONFIG_LOAD_FAILED, ApplicationRefusal
from kriya.config.service import ConfigService
from kriya.context.model import RequestContext
from kriya.context.workspace_identity import workspace_identity
from kriya.policy.authority import (
    ApprovalArtifactError,
    AuthorityPort,
    TrustPathInsideWorkspaceError,
    default_local_approval_path,
    is_approval_current_for,
    load_approval_artifact,
    resolve_with_approval,
    review_fingerprint,
    validate_trust_path_outside_workspace,
)

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


def _state(config: ConfigService):
    """The folder's resolution state; the caller is ``inside`` the folder. Typed CONFIG_LOAD_FAILED on failure."""
    try:
        return config.resolution_state()
    except Exception as error:  # noqa: BLE001 - a malformed or unreadable kriya.yaml is a typed refusal, never a traceback (as execution.preflight)
        raise ApplicationRefusal(CONFIG_LOAD_FAILED, f"the configuration cannot be read: {type(error).__name__}: {str(error)[:300]}") from error


class ConfigurationService:
    def __init__(self, authority: Optional[AuthorityPort] = None, config: Optional[ConfigService] = None) -> None:
        self._authority = authority if authority is not None else AuthorityPort()
        self._config = config if config is not None else ConfigService()

    def approval_status(self, ctx: RequestContext, workspace: str) -> Dict[str, Any]:
        """The approval state of the folder ``workspace`` (canonical; ``ctx.workspace``). Read-only."""
        with self._config.inside(ctx):
            state = _state(self._config)
        trust_file = self._config.trust_file()
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

    def approve(self, ctx: RequestContext, reviewed: str) -> Dict[str, Any]:
        """Record the approval of exactly the configuration ``reviewed`` names (its review fingerprint), through
        ``AuthorityPort.grant_config_approval``: resolved, compared and written under one hold of the working-directory lock."""
        trust_file = self._config.trust_file()
        with self._config.inside(ctx):  # the working directory cannot change between the comparison and the write
            return self._approve(_state(self._config), reviewed, trust_file)

    def _approve(self, state, reviewed: str, trust_file: Optional[str]) -> Dict[str, Any]:
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
            artifact, _path = self._authority.grant_config_approval(state.violations, state.config_dict, state.workspace_root)
        except OSError as error:
            raise ApplicationRefusal(APPROVAL_UNAVAILABLE, f"The approval could not be saved: {error.strerror or type(error).__name__}", reason="store_unwritable") from error
        return _result(state, "approved", artifact.set_digest[:16])


__all__ = ["ConfigurationService", "NO_VALUES_NOTE", "SCOPE"]
