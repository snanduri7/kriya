"""D4 model-free reproducer: which main class does `mvn exec:java` run when the
candidate POM's exec-maven-plugin configuration names one class and Kriya's
command names another (-Dexec.mainClass)?

Live (KNOW-A runtime-2, 20261001T162418-01a90bf2): POM exec-maven-plugin 3.1.0
<mainClass>com.example.IgniteSpringDemo</mainClass>; command
`mvn -e exec:java -Dexec.mainClass=com.example.IgniteDemoApp`; Maven loaded
IgniteSpringDemo -> ClassNotFoundException.

Real host Maven, no model. Variants:
  pom-config : POM plugin <configuration><mainClass>demo.NonexistentA</mainClass>
  no-config  : control, POM declares the plugin without a mainClass
Each: compile, then the exact live command form with -X; reports what Maven's
own mojo-configuration dump says mainClass was, and which class it tried.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
<groupId>demo</groupId><artifactId>d4</artifactId><version>1</version>
<properties><maven.compiler.release>17</maven.compiler.release></properties>
<build><plugins><plugin><groupId>org.codehaus.mojo</groupId><artifactId>exec-maven-plugin</artifactId>
<version>3.1.0</version>{config}</plugin></plugins></build></project>
"""
B = 'package demo;\npublic class ExistingB { public static void main(String[] a) { System.out.println("EXISTING_B_RAN"); } }\n'
CMD = ["mvn", "-X", "-e", "exec:java", "-Dexec.mainClass=demo.ExistingB"]


def run(variant: str, config: str) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"kriya-d4-{variant}-") as tmp:
        ws = Path(tmp)
        (ws / "src/main/java/demo").mkdir(parents=True)
        (ws / "pom.xml").write_text(POM.format(config=config))
        (ws / "src/main/java/demo/ExistingB.java").write_text(B)
        subprocess.run(["mvn", "-q", "clean", "compile"], cwd=ws, check=True, capture_output=True)
        out = subprocess.run(CMD, cwd=ws, capture_output=True, text=True)
        text = out.stdout + out.stderr
        configured = re.findall(r"\(f\) mainClass = (\S+)", text)
        plugin = re.findall(r"--- exec:(\S+):java", text)
        missing = re.findall(r"ClassNotFoundException: (\S+)", text)
        return {"variant": variant, "argv": CMD, "pom_plugin_configuration": config or "(none)",
                "plugin_version_used": plugin[:1], "mojo_configuration_mainClass": configured[:1],
                "class_not_found": missing[:1], "existing_b_ran": "EXISTING_B_RAN" in text,
                "returncode": out.returncode}


if __name__ == "__main__":
    print(json.dumps([run("pom-config", "<configuration><mainClass>demo.NonexistentA</mainClass></configuration>"),
                      run("no-config", "")], indent=1))
    sys.exit(0)
