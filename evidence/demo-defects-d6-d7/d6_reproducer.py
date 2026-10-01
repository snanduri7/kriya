"""D6 model-free reproducer: KNOW B's exact candidate (runtime-4, run-b-20261001T122407Z), its exact runtime
command, run through PolymorphicValidator.run_app_sequence with the verification tree bound, as attempt.py does.
Host execution (no container); needs Maven and Ignite 2.18 in the local repository.
Usage: python d6_reproducer.py <repo root>"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT))
from kriya.config.config import AutonomyConfig  # noqa: E402
from kriya.tools.validate import PolymorphicValidator  # noqa: E402
from kriya.workflow.file_integrity import VerificationTreeBinding  # noqa: E402

CANDIDATE = ["pom.xml", "src/main/resources/ignite-config.xml", "src/main/java/com/example/IgniteDemoApp.java"]
with tempfile.TemporaryDirectory() as tmp:
    ws = os.path.join(tmp, "ws")
    shutil.copytree(Path(__file__).parent / "fixtures" / "knowb_candidate", ws)
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)  # the candidate is untracked, as in a fresh unit
    validator = PolymorphicValidator(ws, autonomy_cfg=AutonomyConfig(contained_execution_required=False))
    validator.tree_binding = VerificationTreeBinding(ws, [], CANDIDATE)
    report = {}
    try:
        result = validator.run_app_sequence(
            [["mvn", "-e", "-q", "compile", "exec:exec", "-Dexec.mainClass=com.example.IgniteDemoApp"]])
        report.update(gate="PASSED", success=result["success"],
                      verification_pass="[VERIFICATION] PASS" in result["output"],
                      ignite_started="Ignite node started OK" in result["output"],
                      runtime_artifacts=result.get("runtime_artifacts"))
    except Exception as exc:  # the typed stop under test
        report.update(gate="STOPPED", stop=type(exc).__name__,
                      reason_code=getattr(getattr(exc, "failure", None), "diagnostics", {}).get("reason_code"),
                      likely_files=getattr(getattr(exc, "failure", None), "likely_files", None))
    report["left_in_workspace"] = sorted(
        str(p.relative_to(ws)) for p in Path(ws).rglob("*")
        if p.is_file() and not str(p.relative_to(ws)).startswith((".git/", ".kriya/", "target/"))
        and str(p.relative_to(ws)) not in CANDIDATE)
    print(json.dumps(report, indent=1))
