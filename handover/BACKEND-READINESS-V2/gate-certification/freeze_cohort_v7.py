#!/usr/bin/env python3
"""Freeze record for the final 12-case cohort: everything the owner listed, measured from files (no model call), written once
to `<V2>/cohort-final/FREEZE.json` (refuses to overwrite). Run BEFORE the first case.

    PYTHONPATH=$PWD .venv/bin/python handover/BACKEND-READINESS-V2/gate-certification/freeze_cohort.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

HOME = Path.home() / "kriya-m1-live"
K = Path(__file__).resolve().parents[3]
V2 = HOME / "backend-readiness-v2"
OUT = V2 / "cohort-final-2" / "FREEZE.json"
TASKS = [("T1", HOME / "backend-readiness-001/tasks"), ("T2", HOME / "backend-readiness-001/tasks"), ("T3", HOME / "backend-readiness-001/tasks"),
         ("T4", HOME / "backend-readiness-001/tasks"), ("T5", HOME / "backend-readiness-001/tasks"), ("T6", HOME / "backend-readiness-001/tasks"),
         ("S1_A", HOME / "backend-readiness-004/cohort-002/tasks"), ("S2_A", HOME / "backend-readiness-004/cohort-002/tasks"),
         ("S3_A", HOME / "backend-readiness-004/cohort-002/tasks"), ("S4_A", HOME / "backend-readiness-004/cohort-002/tasks"),
         ("S4_B", HOME / "backend-readiness-004/cohort-002/tasks"), ("S6_A", HOME / "backend-readiness-004/cohort-002/tasks")]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=K, capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    if OUT.exists():
        print("ABORT: freeze record exists")
        return 1
    tasks = {}
    for tid, root in TASKS:
        d = root / tid
        cfg = yaml.safe_load((d / "config.yaml").read_text())
        llm = cfg.get("llm", {})
        tasks[tid] = {
            "goal_sha256": sha(d / "goal.txt"), "config_sha256": sha(d / "config.yaml"), "meta_sha256": sha(d / "meta.json"),
            "accept_oracle_sha256": sha(d / "oracle/accept.sh"), "regress_oracle_sha256": sha(d / "oracle/regress.sh"),
            "base_sha": json.loads((d / "meta.json").read_text()).get("mutated_sha"),
            "developer_model": llm.get("model"), "temperature": llm.get("temperature"), "max_tokens": llm.get("max_tokens"),
            "options": (llm.get("extra_body") or {}).get("options"), "fallback_chain": [m.get("model") for m in cfg.get("llm_chain", [])],
            "agent_llms": {k: (v.get("llm") or {}).get("model") for k, v in (cfg.get("agent_llms") or {}).items() if isinstance(v, dict)},
            "endpoint": llm.get("base_url"), "inference_runtime": llm.get("inference_runtime"),
            "self_correction_loop_enabled": cfg.get("autonomy", {}).get("self_correction_loop_enabled"),
        }
    src = lambda rel: sha(K / rel)  # noqa: E731
    record = {
        "frozen_at": time.strftime("%F %T"), "branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"),
        "origin_head": git("rev-parse", "origin/backend/readiness-v2"), "tree_clean": git("status", "--porcelain") == "",
        "full_suite_revision": "9de5404", "full_suite_result": "run2 on 9de5404: 10676 passed, 1 failed (registered TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001, isolated 2/2 green)",
        "product_paths_changed_since_full_suite": [p for p in git("diff", "--name-only", "9de5404", "HEAD").splitlines()
                                                    if not p.startswith("handover/")],
        "ollama": subprocess.run(["ollama", "--version"], capture_output=True, text=True).stdout.strip(),
        "retry_limits": {"max_retries_full_set": 4, "targeted_max_retries": 3, "no_progress_limit": 3},
        "time_budget": "internal generation budget 3600 s with 120 s reserved for gates/review (generate.log line, unchanged)",
        "acceptance_threshold": ">=8/12 GENUINE with FALSE_SUCCESS=0, FALSE_NEGATIVE=0, authority=0, corruption=0, leaks=0",
        "qualification_policy_version": "kriya-qualification/8",
        "versions": {
            "goal_example_compiler": {"EXAMPLE_COMPILER_VERSION": 1, "JAVA_EXAMPLE_COMPILER_VERSION": 1, "GOAL_EXAMPLE_FORMS_VERSION": 1,
                                       "enforced_forms": "java_instance_value (+ legacy static value/raises)",
                                       "source_sha256": {"example_oracle_java": src("kriya/workflow/example_oracle_java.py"),
                                                         "goal_example_forms": src("kriya/workflow/goal_example_forms.py")}},
            "contract_gate": {"contract_feedback_sha256": src("kriya/workflow/contract_feedback.py"), "failure_type": "deterministic_contract_violation"},
            "semantic_safety": {"exception_masking_sha256": src("kriya/workflow/exception_masking.py"),
                                "terminal_gate_service_sha256": src("kriya/workflow/terminal_gate_service.py"),
                                "terminal_commit_sha256": src("kriya/workflow/terminal_commit.py")},
            "environment_preflight": {"oracle_preflight_sha256": sha(K / "handover/BACKEND-READINESS-V2/gate-certification/oracle_preflight.py"),
                                       "evaluate_v3_sha256": sha(K / "handover/BACKEND-READINESS-V2/gate-certification/wrappers/evaluate_v3.sh")},
            "leak_rule": "kriya evidence leak-check (format kriya.hidden_oracle_leak_check/1), unchanged; verdict + hits recorded per case",
            "plan_feasibility": {"edit_feasibility_sha256": src("kriya/workflow/edit_feasibility.py"), "change": "A (aafe352)"},
            "goal_contract_module_ownership": {"jvm_module_ownership_sha256": src("kriya/workflow/jvm_module_ownership.py"), "change": "B (248154b)"},
            "contract_relevance": {"contract_relevance_sha256": src("kriya/workflow/contract_relevance.py"),
                                    "statuses": "RELEVANT_DIRECT_TARGET / RELEVANT_DIRECT_DEPENDENCY / NOT_RELEVANT / UNKNOWN", "product_commit": "9de5404"},
            "baseline_comparison": "suite_attribution / validation_baseline (unchanged since 051e982; source in the tested product revision)",
        },
        "oracles_note": "frozen oracle scripts hashed per task below; cohort manifests: " + ", ".join(
            f"{p.name}={sha(p)[:12]}" for p in [HOME / "backend-readiness-001/COHORT_MANIFEST.json"] if p.exists()),
        "task_order": [t for t, _ in TASKS], "protocol": "serial, one run per case, 120 s cool-down, no reruns, oracle exits classified by "
        "oracle_preflight before use; Kriya success + oracle REJECT is recorded as FALSE_SUCCESS (the cohort continues: all 12 are measured)",
        "tasks": tasks,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, indent=1))
    print(json.dumps({k: record[k] for k in ("branch", "head", "origin_head", "tree_clean", "product_paths_changed_since_full_suite")}, indent=1))
    print("tasks frozen:", len(tasks), "->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
