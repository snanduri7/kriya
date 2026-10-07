"""Deterministic, no-model fallback-compatibility check (PRD-017 / LR-R1-P1 rules, Kriya a049c02) for the qwen3.8
NATIVE-32768 profile: Kriya's own resolve_request_profile + fallback_incompatibilities, for the configured
llm_chain binding, for a generic attempt and for a patch-only attempt. Reads the qualification record from
KRIYA_QUALIFICATION_HOME; the runtime probe reads /api/version, /api/tags and /api/show only (no inference).

usage (from the approved workspace, env-qwen38.sh sourced): python fallback_compat_check.py <config.yaml> <out.json>
"""
import json
import sys

from kriya.config.config import load_config
from kriya.workflow.model_transition import fallback_incompatibilities, resolve_request_profile

PATCH_ONLY_FILES = ("graphify/extractors/engine.py",)  # a path name only; nothing is read


def main(config_path: str, out: str) -> None:
    config = load_config(config_path)
    report = {"runtime_profile": config.runtime_profile, "bindings": []}
    for label, binding in [("primary", config.llm)] + [(f"llm_chain[{i}]", b) for i, b in enumerate(config.llm_chain)]:
        profile = resolve_request_profile(config, binding)
        report["bindings"].append({
            "binding": label,
            "profile": profile.to_dict(),
            "generic_attempt_incompatibilities": fallback_incompatibilities(config, profile),
            "patch_only_attempt_incompatibilities": fallback_incompatibilities(
                config, profile, patch_required_files=PATCH_ONLY_FILES),
        })
    for entry in report["bindings"]:
        entry["compatible"] = not entry["generic_attempt_incompatibilities"] and not entry[
            "patch_only_attempt_incompatibilities"]
    report["status"] = "PASS" if all(entry["compatible"] for entry in report["bindings"]) else "FAIL"
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, default=str)
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
