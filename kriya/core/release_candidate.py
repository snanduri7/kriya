"""PRD-036 release-candidate identity: the one exact thing a final release
certification is evidence about.

It composes identities that already exist, never redefining any of them:

- ``release``: the PLAT-RELEASE-IDENTITY-001 release identity, unchanged:
  the Kriya revision and tree cleanliness, the platform providers and
  capability statuses, the containment backend and its runtime, and the
  environment.
- ``production_config``: the operator production configuration. It
  records the file's content digest, the workspace it is approved for, and
  a digest of its SEC-009 security-relevant field set, taken with the
  effective values after profile expansion. The value digests use a fixed,
  published salt, so the same configuration always gives the same
  identity; values are never stored. The content of every file the config
  points the gates at (static-analysis rule packs) is bound too, so editing
  one changes the identity even though the path stays the same.
- ``models``: every role's every callable model. For each identity the
  role sends (PRD-014), it records the exact runtime digest (PRD-013), the
  inference-settings digest and the current qualification status. It also
  records the Developer primary and first fallback that the live matrix
  runs, and the execution-environment digest.
- ``case_set``: the live matrix case-set version and a digest of its case
  table.

Any change to any of these is a different candidate: a new digest, so any
live-certification streak restarts at 0 (``model_certification.record_trial``).
A candidate is CURRENT only when every recorded field still matches the
host, the release identity itself is CURRENT (a clean, pinned revision),
and every bound model identity is exact and QUALIFIED.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from kriya.core.release_identity import _changes, _digest, compare_release_identity, release_identity

RELEASE_CANDIDATE_VERSION = 1
# A fixed salt makes the security-field digest a stable identity. SEC-009
# approvals keep their own random salt; this digest authorizes nothing.
SECURITY_IDENTITY_SALT = "kriya.release_candidate/1"
CURRENT, STALE = "CURRENT", "STALE"


def production_config_identity(config_path: str) -> Dict[str, Any]:
    """The operator configuration's identity for the current workspace (the
    working directory, as ``load_config`` resolves it), with the effective
    values after profile expansion."""
    from kriya.config.authority_approval import build_security_field_records, compute_set_digest
    from kriya.config.config import resolve_config_state
    from kriya.control.workspace_identity import workspace_identity

    with open(config_path, "rb") as handle:
        content_sha256 = hashlib.sha256(handle.read()).hexdigest()
    state = resolve_config_state(config_path)
    records = build_security_field_records(state.violations, state.config_dict, SECURITY_IDENTITY_SALT)
    providers = ((state.config_dict.get("static_analysis") or {}).get("providers") or {})
    rule_packs = sorted({os.path.realpath(pack) for provider in providers.values() if isinstance(provider, dict)
                         for pack in provider.get("rule_packs") or []})
    return {
        "path": os.path.realpath(config_path),
        "content_sha256": content_sha256,
        "workspace_id": workspace_identity(os.getcwd()),
        "security_field_set_digest": compute_set_digest(records),
        "security_fields": sorted(record.field_path for record in records),
        "referenced_files": {path: _file_sha256(path) for path in rule_packs},
    }


def _file_sha256(path: str) -> str:
    """A referenced file's content digest; a missing or unreadable file is
    recorded as such (the gate itself refuses it at run time)."""
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError as error:
        return f"unreadable:{type(error).__name__}"


def model_identity(cfg: Any, *, resolve_runtime: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """Every role binding's exact runtime, settings and qualification, the
    Developer primary and first fallback, and the execution environment."""
    from kriya.core.execution_environment import environment_for_fingerprint
    from kriya.core.inference_settings import role_inference_identities
    from kriya.core.model_qualification import assess, policy_digest_for, required_capabilities, role_models
    from kriya.core.model_runtime import resolve_configured_model_runtime

    resolve = resolve_runtime or resolve_configured_model_runtime
    runtimes: Dict[str, Any] = {}
    bindings: List[Dict[str, Any]] = []
    for role, models in sorted(role_models(cfg).items()):
        for model in models:
            key = model.casefold()
            if key not in runtimes:
                runtimes[key] = resolve(cfg, model, fresh=True)
            runtime = runtimes[key]
            for label, settings in role_inference_identities(cfg, role, model):
                assessment = assess(runtime, required_capabilities(cfg, role, model), settings=settings,
                                    policy_digest=policy_digest_for(cfg))
                bindings.append({
                    "role": role, "model": model, "identity": label, "runtime_digest": runtime.digest,
                    "runtime_exact": bool(runtime.exact), "inference_settings_digest": settings.digest,
                    "qualification": assessment.status,
                })

    def developer(model: str) -> Dict[str, Any]:
        row = next(b for b in bindings if b["role"] == "developer" and b["model"] == model and b["identity"] == "normal")
        return {"model": model, "runtime_digest": row["runtime_digest"],
                "inference_settings_digest": row["inference_settings_digest"]}

    return {
        "bindings": bindings,
        "developer_primary": developer(cfg.llm.model),
        "developer_fallback": developer(cfg.llm_chain[0].model) if cfg.llm_chain else None,
        "execution_environment": environment_for_fingerprint(runtimes[cfg.llm.model.casefold()]).to_dict(),
    }


def case_set_identity(version: int, cases: Sequence[Sequence[str]]) -> Dict[str, Any]:
    table = [list(case) for case in cases]
    return {"version": version, "case_ids": [case[0] for case in table], "table_digest": _digest({"cases": table})}


def release_candidate_identity(cfg: Any, config_path: str, *, case_set: Mapping[str, Any],
                               source_root: Optional[str] = None,
                               resolve_runtime: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """The candidate identity. ``digest`` covers every material field."""
    release = (release_identity(cfg.autonomy.containment_backend) if source_root is None
               else release_identity(cfg.autonomy.containment_backend, source_root=source_root))
    material = {
        "version": RELEASE_CANDIDATE_VERSION,
        "release": release,
        "production_config": production_config_identity(config_path),
        "models": model_identity(cfg, resolve_runtime=resolve_runtime),
        "case_set": dict(case_set),
    }
    return {**material, "digest": _digest(material)}


def candidate_digest_valid(candidate: Mapping[str, Any]) -> bool:
    return candidate.get("digest") == _digest({k: v for k, v in candidate.items() if k != "digest"})


def compare_release_candidate(recorded: Mapping[str, Any], current: Mapping[str, Any]) -> Dict[str, Any]:
    """CURRENT only when nothing material changed, the release identity is
    CURRENT, and every bound model identity is exact and QUALIFIED. STALE
    otherwise, naming every reason."""
    changes = _changes(dict(recorded), dict(current), "")
    if not candidate_digest_valid(recorded):
        changes.append("digest")
    # Field comparison skips nested ``digest`` keys (an environment digest is
    # one); the composite digest still covers them, so any difference blocks.
    if recorded.get("digest") != current.get("digest"):
        changes.append("digest.mismatch")
    release = compare_release_identity(recorded.get("release") or {}, current.get("release") or {})
    changes += [f"release.{change}" for change in release["changes"]]
    for row in (recorded.get("models") or {}).get("bindings") or []:
        if row.get("qualification") != "QUALIFIED" or row.get("runtime_exact") is not True:
            changes.append(f"models.unqualified.{row.get('role')}.{row.get('model')}.{row.get('identity')}")
    return {"status": CURRENT if not changes else STALE, "changes": sorted(set(changes))}
