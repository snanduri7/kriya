"""PRD-035: the model-certification report, built from the case records the
live matrix (tests/test_live_prd035_certification.py) collects.

``content`` holds the identity (exact runtime, inference settings,
execution environment, case-set version), the tier (``target_production``
only for the qualified target identity on an exact, observable environment;
``wiring`` otherwise, which never certifies) and every case with its verdict
and evidence. Status is CERTIFIED only when the tier is target_production and
every case of the case set PASSED; a missing case is NOT_RUN and fails.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

REPORT_SCHEMA = "kriya.model_certification/1"
CASES: Tuple[Tuple[str, str], ...] = (
    ("C1", "bug_fix"), ("C2", "multi_file_feature"), ("C3", "brownfield_extension"),
    ("C4", "exact_requirement"), ("C5", "targeted_retry"), ("C6", "fallback_transition"),
    ("C7", "contained_compile_test"), ("C8", "pre_post_regression"), ("C9", "resume_safety"),
    ("C10", "malicious_instruction"), ("C11", "static_analysis_enabled"),
)
TARGET, WIRING = "target_production", "wiring"
IDENTITY_SCHEMA = "kriya.model_certification.kriya_identity/1"


def _git(root: str, *args: str) -> str:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=False).stdout.strip()


def kriya_identity(root: str, config_path: Optional[str]) -> Dict[str, Any]:
    """The Kriya executable identity a certification binds to (PRD-035 revision binding): the Git commit, its tree, the ``kriya/``
    and ``plugins/`` subtrees, whether the tracked worktree equals that commit, and the sha256 of the production configuration file."""
    config = None
    if config_path:
        try:
            with open(config_path, "rb") as handle:
                config = {"name": os.path.basename(config_path), "sha256": hashlib.sha256(handle.read()).hexdigest()}
        except OSError:
            config = None
    return {"schema": IDENTITY_SCHEMA, "commit": _git(root, "rev-parse", "HEAD"), "tree": _git(root, "rev-parse", "HEAD^{tree}"),
            "kriya_tree": _git(root, "rev-parse", "HEAD:kriya"), "plugins_tree": _git(root, "rev-parse", "HEAD:plugins"),
            "tracked_worktree_clean": _git(root, "status", "--porcelain", "--untracked-files=no") == "", "config": config}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def build_report(results: Iterable[Mapping[str, Any]], identity_of_kriya: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    by_id: Dict[str, Mapping[str, Any]] = {}
    for result in results:
        by_id[result["case_id"]] = result
    cases: List[Dict[str, Any]] = []
    for case_id, task_class in CASES:
        result = by_id.get(case_id)
        if result is None:
            cases.append({"case_id": case_id, "task_class": task_class, "verdict": "NOT_RUN"})
            continue
        cases.append({**result.get("record", {}), "case_id": case_id, "task_class": task_class,
                      "verdict": result["verdict"]})
    identities = {json.dumps(c.get("identity"), sort_keys=True) for c in cases if c.get("identity")}
    identity = json.loads(next(iter(identities))) if len(identities) == 1 else None
    environments = {json.dumps(c.get("environment"), sort_keys=True) for c in cases if c.get("environment")}
    environment = json.loads(next(iter(environments))) if len(environments) == 1 else {}
    exact_env = bool(environment.get("exact"))
    tier = (TARGET if identity and identity.get("qualification") == "QUALIFIED" and exact_env
            and len(identities) == 1 and len(environments) == 1 else WIRING)
    passed = all(c["verdict"] == "PASSED" for c in cases)
    content = {
        "schema": REPORT_SCHEMA, "case_set_version": 1, "tier": tier,
        "status": "CERTIFIED" if passed and tier == TARGET else "FAILED",
        "identity": identity, "identity_consistent": len(identities) == 1 and len(environments) == 1,
        "execution_environment": dict(environment),
        "summary": {v: sum(1 for c in cases if c["verdict"] == v) for v in ("PASSED", "FAILED", "SKIPPED", "NOT_RUN")},
        "cases": cases,
    }
    if identity_of_kriya is not None:
        # {"start": ..., "end": ...} (kriya_identity() when the matrix began and when it ended): part of the content, so the report digest, and
        # through it the stored record (record.report_digest), bind to it. scripts/verify_model_certification.py refuses a mismatch.
        content["kriya_identity"] = dict(identity_of_kriya)
    return {"content": content, "content_digest": _digest(content)}


def render_markdown(report: Mapping[str, Any]) -> str:
    content = report["content"]
    identity = content.get("identity") or {}
    lines = [
        f"# Kriya live model certification: **{content['status']}** ({content['tier']})", "",
        f"- Model: `{identity.get('model')}`; runtime `{identity.get('runtime_fingerprint')}`; "
        f"inference settings `{identity.get('inference_settings_digest')}`; qualification {identity.get('qualification')}",
        f"- Execution environment: `{content['execution_environment'].get('digest')}` "
        f"({content['execution_environment'].get('accelerator_model')}, {content['execution_environment'].get('system_memory_class_gib')} GiB)",
        f"- Content digest: `{report['content_digest']}`; case set v{content['case_set_version']}",
        "", "| Case | Class | Verdict | Final | First-pass compile | Retries (full/targeted) | Fallbacks | Tokens in/out | Wall s | Commit | Evidence |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in content["cases"]:
        first = c.get("first_pass_compile") or {}
        first_text = first.get("value") if first.get("status") == "MEASURED" else first.get("status", "-")
        retries = c.get("retries") or {}
        tokens = c.get("tokens") or {}
        evidence = {k: c[k] for k in ("hidden_acceptance_passed", "static_analysis", "baseline_events",
                                      "resume_decision_recorded", "secret_leaked", "developer_models",
                                      "static_analysis_evidence_bound") if k in c}
        lines.append(
            f"| {c['case_id']} | {c['task_class']} | {c['verdict']} | {c.get('final_success', '-')} | {first_text} | "
            f"{retries.get('full_set', '-')}/{retries.get('targeted', '-')} | {c.get('fallback_transitions', '-')} | "
            f"{tokens.get('input', '-')}/{tokens.get('output', '-')} | {c.get('wall_seconds', '-')} | "
            f"{','.join(c.get('commit_results') or []) or '-'} | `{json.dumps(evidence, sort_keys=True)}` |")
    return "\n".join(lines) + "\n"


def write_report(directory: str, report: Mapping[str, Any]) -> Tuple[str, str]:
    os.makedirs(directory, exist_ok=True)
    paths = (os.path.join(directory, "model-certification.json"), os.path.join(directory, "model-certification.md"))
    with open(paths[0], "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    with open(paths[1], "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    return paths
