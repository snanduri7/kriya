"""No-inference identity dry check for the qwen3-coder role-placement /8 qualification (Kriya a049c02).

Reproduces exactly how `kriya model qualify --model M` (no --role) selects identities and builds the runtime
fingerprint (cli.model_qualify -> run_qualification -> qualification_config -> resolve_configured_model_runtime),
and compares it with how `kriya model status` assesses each role. Probes /api/version, /api/tags, /api/show only.

usage (from the qualification workspace, env sourced): python identity_dry_check.py <config.yaml> <model> <out.json>
"""
import json
import sys

from kriya.config.config import load_config
from kriya.core.inference_settings import role_inference_identities
from kriya.core.model_capabilities import capabilities_for_model
from kriya.core.model_qualification import (
    qualification_config,
    required_capabilities,
    role_models,
)
from kriya.core.model_runtime import resolve_configured_model_runtime


def main(config_path, model, out):
    cfg = load_config(config_path)
    users = [role for role, models in role_models(cfg).items() if any(m.casefold() == model.casefold() for m in models)]
    identities = {}
    for role in users:
        for label, settings in role_inference_identities(cfg, role, model):
            identities.setdefault(settings.digest, (settings, []))[1].append(role if label == "normal" else f"{role} ({label})")
    report = {"model": model, "roles_using_model": users, "qualify_identities": [], "status_identities": []}
    for settings, roles in identities.values():
        qcfg = qualification_config(cfg, model, None, settings=settings)
        fp = resolve_configured_model_runtime(qcfg, model, fresh=True)
        report["qualify_identities"].append({
            "roles": roles, "settings_digest": settings.digest, "settings": settings.to_dict(),
            "fingerprint_digest": fp.digest, "exact": fp.exact,
            "capability_profile": capabilities_for_model(qcfg, model).to_dict()
            if hasattr(capabilities_for_model(qcfg, model), "to_dict") else str(capabilities_for_model(qcfg, model)),
            "fingerprint": fp.to_dict() if hasattr(fp, "to_dict") else str(fp)})
    for role in users:
        fp = resolve_configured_model_runtime(cfg, model, fresh=True)
        for label, settings in role_inference_identities(cfg, role, model):
            report["status_identities"].append({"role": role, "identity": label, "settings_digest": settings.digest,
                                                "fingerprint_digest": fp.digest,
                                                "required": list(required_capabilities(cfg, role, model))})
    q = {(i["fingerprint_digest"], i["settings_digest"]) for i in report["qualify_identities"]}
    s = {(i["fingerprint_digest"], i["settings_digest"]) for i in report["status_identities"]}
    report["qualify_covers_status"] = s <= q
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, default=str)
    print(json.dumps({k: report[k] for k in ("roles_using_model", "qualify_covers_status")}))
    for i in report["qualify_identities"]:
        print("QUALIFY", i["roles"], i["settings_digest"], i["fingerprint_digest"], i["exact"])
    for i in report["status_identities"]:
        print("STATUS ", i["role"], i["identity"], i["settings_digest"], i["fingerprint_digest"])


if __name__ == "__main__":
    main(*sys.argv[1:4])
