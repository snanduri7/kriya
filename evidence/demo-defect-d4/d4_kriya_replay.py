"""D4 replay through Kriya's real runtime path (no model): the live-shaped
project (POM exec-maven-plugin 3.1.0 <mainClass> naming a class that does not
exist; the source declares another), the model's live command form, the D3
prerequisite, then Kriya's own diagnosis and classification. Also the three
other ownership cases."""
import json
import tempfile
from pathlib import Path

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.acceptance import classify_runtime_entrypoint, runtime_verification_infrastructure_reason

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
<groupId>demo</groupId><artifactId>d4</artifactId><version>1</version>
<properties><maven.compiler.release>17</maven.compiler.release></properties>
<build><plugins><plugin><groupId>org.codehaus.mojo</groupId><artifactId>exec-maven-plugin</artifactId>
<version>3.1.0</version>{config}</plugin></plugins></build></project>
"""
B = 'package demo;\npublic class ExistingB { public static void main(String[] a) { System.out.println("B_RAN"); } }\n'
CASES = {
    "A candidate pom names a nonexistent class": ("<configuration><mainClass>demo.NonexistentA</mainClass></configuration>",
                                                  ["mvn", "-e", "exec:java", "-Dexec.mainClass=demo.ExistingB"]),
    "B Kriya's command names a nonexistent class": ("", ["mvn", "-e", "exec:java", "-Dexec.mainClass=demo.WrongMain"]),
    "valid (control)": ("", ["mvn", "-e", "exec:java", "-Dexec.mainClass=demo.ExistingB"]),
}
report = {}
for name, (config, command) in CASES.items():
    with tempfile.TemporaryDirectory(prefix="kriya-d4-replay-") as tmp:
        ws = Path(tmp)
        (ws / "src/main/java/demo").mkdir(parents=True)
        (ws / "pom.xml").write_text(POM.format(config=config))
        (ws / "src/main/java/demo/ExistingB.java").write_text(B)
        result = PolymorphicValidator(str(ws), autonomy_cfg=AutonomyConfig()).run_app_sequence([command])
        diagnosis = result.get("entrypoint_diagnosis")
        report[name] = {"runtime_prerequisite": result["runtime_prerequisite"], "success": result["success"],
                        "diagnosis": diagnosis and {k: v for k, v in diagnosis.items() if k != "command"},
                        "ownership": classify_runtime_entrypoint(diagnosis, result["output"]),
                        "pre_d4_infrastructure_reason": runtime_verification_infrastructure_reason(result)}
print(json.dumps(report, indent=1))
