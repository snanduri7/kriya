"""B3 prerequisite: B2-c JVM acceptance through the production OCI containment path (harness; no model).

Deterministic fixture (tests/_b2c_fixtures.py), `contained_execution_required: true`, backend `oci`, a fresh empty
Maven cache under a fresh state root. Two candidates (correct, wrong), each run twice: run 1 needs dependency
acquisition, run 2 must be served offline from the cache. Every command the validator issues is logged with its
network authority.

usage: PYTHONPATH=<checkout>:<checkout>/tests python contained_jvm_measurement.py <out.json>
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from _b2c_fixtures import ACCEPTANCE, EXACT_GOAL, TARGET, base_revision, candidate, maven_workspace

from kriya.config.config import AppConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow.acceptance_jvm import run_java_acceptance
from kriya.workflow.requirements import derive_requirements


def main(out):
    work = Path(tempfile.mkdtemp(prefix="b3-oci-jvm-"))
    os.environ["KRIYA_STATE_DIR"] = str(work / "state")
    autonomy = AppConfig().autonomy
    autonomy.contained_execution_required = True
    autonomy.containment_backend = "oci"
    commands = []
    original = PolymorphicValidator._run_cmd_with_timeout

    def logged(self, cmd, cwd, *args, **kwargs):
        started = time.time()
        result = original(self, cmd, cwd, *args, **kwargs)
        commands.append({"argv": [a for a in cmd if not a.startswith("-Dmaven.repo.local")][:8],
                         "network": str(kwargs.get("network", "DENIED")), "acquisition": kwargs.get("acquisition", False),
                         "returncode": result.get("returncode"), "seconds": round(time.time() - started, 1),
                         "containment": (result.get("containment") or {}).get("backend") if isinstance(
                             result.get("containment"), dict) else result.get("containment")})
        return result

    PolymorphicValidator._run_cmd_with_timeout = logged
    results = {}
    try:
        ws = maven_workspace(work / "ws")
        (work / "KriyaAcceptanceTest.java").write_text(ACCEPTANCE)
        artifact = ao.load_acceptance(str(work / "KriyaAcceptanceTest.java"), derive_requirements(EXACT_GOAL),
                                      str(work / "artifacts"))
        for variant in ("correct", "wrong"):
            cand = candidate(ws, work / variant, variant)
            for attempt in (1, 2):
                commands.clear()
                started = time.time()
                run = run_java_acceptance(artifact, str(cand), candidate_paths=[TARGET], base_revision=base_revision(ws),
                                          validator_factory=lambda root: PolymorphicValidator(
                                              root, original_workspace_path=str(ws), autonomy_cfg=autonomy))
                judgment = ao.judge_acceptance(artifact, run)["REQ-1"]
                key = f"{variant}-run{attempt}"
                results[key] = {
                    "code": judgment.code, "reason": judgment.reason[:400], "seconds": round(time.time() - started, 1),
                    "refusal": run.refusal.reason_code if run.refusal else None,
                    "report_complete": bool(run.report and run.report.complete),
                    "report_runner": run.report.runner if run.report else None,
                    "case_results": judgment.evidence.get("case_results"),
                    "execution": {k: run.result.get(k) for k in ("execution_mode", "containment", "egress")
                                  if isinstance(run.result, dict) and k in run.result},
                    "commands": list(commands),
                }
                print(key, json.dumps(results[key], default=str)[:1500], flush=True)
    finally:
        PolymorphicValidator._run_cmd_with_timeout = original
        shutil.rmtree(work, ignore_errors=True)
    Path(out).write_text(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main(sys.argv[1])
