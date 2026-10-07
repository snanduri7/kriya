"""FS-1C2 B2-a: one acceptance run per A1 candidate under real OCI containment (harness; no model).

The production profile seals autonomy.contained_execution_required: the project interpreter is the venv Kriya builds
inside the container from the candidate's requirements.txt (registry-scoped acquisition), and the runner sees the
container's workdir, not the host path. Measures that the B2-a runner works there unchanged.

usage: PYTHONPATH=<b2a checkout> python contained_acceptance_measurement.py <out.json>
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
from _b2a_fixtures import A1_ACCEPTANCE, A1_GOAL, freezegun_project, write_files  # noqa: E402

from kriya.config.config import AppConfig  # noqa: E402
from kriya.tools.validate import PolymorphicValidator  # noqa: E402
from kriya.workflow import acceptance_oracle as ao  # noqa: E402
from kriya.workflow.requirements import derive_requirements  # noqa: E402


def main(out):
    autonomy = AppConfig().autonomy
    autonomy.contained_execution_required = True
    autonomy.containment_backend = "oci"
    work = Path(tempfile.mkdtemp(prefix="b2a-oci-"))
    results = {}
    try:
        (work / "acceptance.py").write_text(A1_ACCEPTANCE)
        artifact = ao.load_acceptance(str(work / "acceptance.py"), derive_requirements(A1_GOAL), str(work / "state"))
        for variant in ("helper_only", "correct"):
            root = freezegun_project(work / variant, variant)
            write_files(root, {"requirements.txt": "pytest\n"})  # the real freezegun pins python-dateutil; stand-in here
            run = ao.run_acceptance(artifact, str(root), candidate_paths=["freezegun/api.py"],
                                    validator_factory=lambda r=root: PolymorphicValidator(str(r), autonomy_cfg=autonomy))
            judgment = ao.judge_acceptance(artifact, run)["REQ-1"]
            results[variant] = {
                "code": judgment.code, "reason": judgment.reason, "case_results": judgment.evidence.get("case_results"),
                "report_complete": bool(run.report and run.report.complete),
                "observed_root": (run.observations or {}).get("root"),
                "candidate_module": ((run.observations or {}).get("modules") or {}).get("freezegun"),
                "plugins_outside_pytest": [p for p in (run.observations or {}).get("plugins", [])
                                           if p != "__main__" and not p.startswith("_pytest.")],
                "containment": {k: run.result.get(k) for k in ("containment", "execution_mode", "egress")
                                if k in run.result},
                "pycache_in_candidate": [str(p.relative_to(root)) for p in root.rglob("__pycache__")
                                         if ".kriya" not in p.parts],
            }
            print(variant, json.dumps(results[variant], default=str), flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    Path(out).write_text(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main(sys.argv[1])
