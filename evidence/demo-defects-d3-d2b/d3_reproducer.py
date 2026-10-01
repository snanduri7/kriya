"""D3 model-free reproducer (KNOW-A 2026-10-01, run 20261001T141456-fe3a1c9d).

The measured live sequence, with Kriya's own code and real Maven, no model:
1. a greenfield Maven app (no .gitignore) committed in a git workspace;
2. Kriya's sandbox worktree (worktree.create_git_worktree) - compiled there
   through Kriya's own compile gate (PolymorphicValidator.run_compile_check);
3. the class file exists under target/classes;
4. the next work unit's reset: create_git_worktree on the same workspace;
5. whether target/classes/.../App.class still exists;
6. the verification-only work unit's runtime check: run_app_sequence with the
   model's command (mvn -e exec:java, no compile);
7. Kriya's classification of the result.
Usage: python d3_reproducer.py [--ignored]   (--ignored adds target/ to .gitignore)
Host-mode Maven (contained_execution_required=False): the mechanism under test
(reset + runtime step with no build) does not depend on containment.
"""
import json
import os
import subprocess
import sys
import tempfile

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.acceptance import runtime_verification_infrastructure_reason
from kriya.workflow.worktree import create_git_worktree

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
<groupId>demo</groupId><artifactId>d3</artifactId><version>1</version>
<properties><maven.compiler.release>17</maven.compiler.release>
<project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties></project>
"""
APP = 'package demo;\npublic class App { public static void main(String[] a) { System.out.println("D3_APP_RAN"); } }\n'
CLASS = os.path.join("target", "classes", "demo", "App.class")


def main() -> None:
    ignored = "--ignored" in sys.argv
    report = {"target_ignored": ignored}
    with tempfile.TemporaryDirectory(prefix="kriya-d3-") as tmp:
        ws = os.path.realpath(os.path.join(tmp, "workspace"))
        os.makedirs(os.path.join(ws, "src/main/java/demo"))
        open(os.path.join(ws, "pom.xml"), "w").write(POM)
        open(os.path.join(ws, "src/main/java/demo/App.java"), "w").write(APP)
        if ignored:
            open(os.path.join(ws, ".gitignore"), "w").write("target/\n")
        for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=d@x", "-c", "user.name=d", "commit", "-qm", "app"]):
            subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)
        sandbox = create_git_worktree(ws)  # work unit N
        validator = PolymorphicValidator(sandbox, autonomy_cfg=AutonomyConfig(contained_execution_required=False))
        compiled = validator.run_compile_check(["src/main/java/demo/App.java"])
        report["compile_success"] = compiled["success"]
        report["class_after_compile"] = os.path.exists(os.path.join(sandbox, CLASS))
        reset = create_git_worktree(ws)  # work unit N+1: the same reset Kriya runs per subtask
        report["same_sandbox"] = reset == sandbox
        report["class_after_reset"] = os.path.exists(os.path.join(sandbox, CLASS))
        runtime = PolymorphicValidator(sandbox, autonomy_cfg=AutonomyConfig(contained_execution_required=False))
        result = runtime.run_app_sequence([["mvn", "-e", "exec:java", "-Dexec.mainClass=demo.App"]])
        report["runtime_success"] = result["success"]
        report["app_ran"] = "D3_APP_RAN" in result["output"]
        report["class_not_found"] = "ClassNotFoundException: demo.App" in result["output"]
        report["classification"] = runtime_verification_infrastructure_reason(result)
        report["runtime_prerequisite"] = result.get("runtime_prerequisite")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
