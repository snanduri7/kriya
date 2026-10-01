"""Required PRD-011 live containment proof (real Docker images/runtime)."""
import json
import os
from pathlib import Path

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.process import ProcessController
from kriya.tools.validate import PolymorphicValidator

pytestmark = pytest.mark.live_model


def _config() -> AutonomyConfig:
    return AutonomyConfig(
        contained_execution_required=True,
        containment_backend="oci",
        sandbox_cpu_seconds=120,
        sandbox_memory_mb=1024,
    )


def _run_with_validator(validator: PolymorphicValidator, command: list[str]):
    profile, backend = validator.build_containment_profile_and_backend()
    return ProcessController().run(
        command, cwd=validator.workspace_path, timeout=120,
        containment_profile=profile, containment_backend=backend,
    )


def test_live_java17_and_python_projects_record_exact_container_toolchains(tmp_path):
    java_root = tmp_path / "java17"
    java_source = java_root / "src" / "main" / "java" / "demo" / "App.java"
    java_source.parent.mkdir(parents=True)
    (java_root / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion><groupId>demo</groupId>"
        "<artifactId>app</artifactId><version>1</version><properties>"
        "<maven.compiler.source>17</maven.compiler.source>"
        "<maven.compiler.target>17</maven.compiler.target>"
        "</properties></project>"
    )
    java_source.write_text(
        'package demo; public class App { public static void main(String[] a) { System.out.println("java17-ok"); } }'
    )
    java_validator = PolymorphicValidator(str(java_root), autonomy_cfg=_config())
    java_compile = java_validator.run_compile_check(["src/main/java/demo/App.java"])
    assert java_compile["success"], java_compile["output"]
    java_run = _run_with_validator(
        java_validator, ["java", "-cp", "target/classes", "demo.App"],
    )
    assert java_run.returncode == 0 and "java17-ok" in java_run.stdout, java_run.stderr

    python_root = tmp_path / "python312"
    python_root.mkdir()
    (python_root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1"\nrequires-python = "==3.12.*"\n'
    )
    (python_root / "app.py").write_text('print("python312-ok")\n')
    python_validator = PolymorphicValidator(str(python_root), autonomy_cfg=_config())
    python_compile = python_validator.run_compile_check(["app.py"])
    assert python_compile["success"], python_compile["output"]
    python_run = _run_with_validator(python_validator, ["python3", "app.py"])
    assert python_run.returncode == 0 and "python312-ok" in python_run.stdout, python_run.stderr

    evidence = {
        "java17": {
            "compile": java_compile["toolchain_identity"],
            "run": java_run.toolchain_identity,
            "stdout": java_run.stdout.strip(),
        },
        "python312": {
            "compile": python_compile["toolchain_identity"],
            "run": python_run.toolchain_identity,
            "stdout": python_run.stdout.strip(),
        },
    }
    evidence_dir = Path(os.environ.get("KRIYA_PRD011_EVIDENCE_DIR", "handover/evidence/PRD-011/user-live"))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / "toolchain-parity.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"PRD-011 live evidence: {evidence_path.resolve()}")

    for project in evidence.values():
        assert project["compile"]["image_digest"].startswith("sha256:")
        assert project["run"]["image_digest"] == project["compile"]["image_digest"]
