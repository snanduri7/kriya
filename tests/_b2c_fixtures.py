"""FS-1C2 B2-c fixtures: a small Maven + JUnit 5 project (versions already in the local Maven repository: JUnit
5.10.2, Surefire 3.2.5, compiler 3.13.0, resources 3.3.1), as a git workspace whose HEAD is the run's base, and
candidate trees as git worktrees of it (the shape a run's candidate has)."""
import subprocess
from pathlib import Path

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
<groupId>demo</groupId><artifactId>demo</artifactId><version>1</version>
<properties><maven.compiler.release>17</maven.compiler.release>
<project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties>
<dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId>
<version>5.10.2</version><scope>test</scope></dependency></dependencies>
<build><plugins>
<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId><version>3.13.0</version></plugin>
<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-resources-plugin</artifactId><version>3.3.1</version></plugin>
<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><version>3.2.5</version></plugin>
</plugins></build></project>
"""
BASE_CALC = "package demo;\n\npublic final class Calc {\n    public static int twice(int value) {\n        return 2 * value;\n    }\n}\n"
EXISTING_TEST = ("package demo;\n\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n\n"
                 "import org.junit.jupiter.api.Test;\n\nclass CalcTest {\n    @Test\n    void twice() {\n"
                 "        assertEquals(4, Calc.twice(2));\n    }\n}\n")
CLAMP = {
    "correct": "        return Math.max(min, Math.min(max, value));\n",
    "wrong": "        return value;\n",
    "throws": "        throw new IllegalStateException(\"clamp is not supported\");\n",
}
TARGET = "src/main/java/demo/Calc.java"

# Enumerated (B2-COV EXACT): the cases are exactly the stated examples.
EXACT_GOAL = ("Add a static clamp(value, min, max) method to Calc in src/main/java/demo/Calc.java so that "
              "`Calc.clamp(0, 1, 5)` returns 1 and `Calc.clamp(9, 1, 5)` returns 5.\n")
# A rule over its parameters (B2-COV GENERAL).
GENERAL_GOAL = ("Add a static clamp(value, min, max) method to Calc in src/main/java/demo/Calc.java that returns min "
                "when value is less than min, max when value is greater than max and value otherwise.\n")
ACCEPTANCE = """package demo;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class KriyaAcceptanceTest {
    // kriya_requirement: REQ-1
    @Test
    void clampsBelowTheMinimum() {
        assertEquals(1, Calc.clamp(0, 1, 5));
    }

    // kriya_requirement: REQ-1
    @Test
    void clampsAboveTheMaximum() {
        assertEquals(5, Calc.clamp(9, 1, 5));
    }
}
"""
CASES = ["demo.KriyaAcceptanceTest.clampsAboveTheMaximum", "demo.KriyaAcceptanceTest.clampsBelowTheMinimum"]
INJECTION = "src/test/java/demo/KriyaAcceptanceTest.java"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def maven_workspace(root: Path, with_calc: bool = True) -> Path:
    """``with_calc=False``: the candidate creates Calc.java (the direct workflow's
    scripted Developer writes whole files, which the operation contract allows
    only for a new file)."""
    files = ({"pom.xml": POM, TARGET: BASE_CALC, "src/test/java/demo/CalcTest.java": EXISTING_TEST} if with_calc
             else {"pom.xml": POM})
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def calc_with_clamp(variant: str) -> str:
    return BASE_CALC.replace(
        "    }\n}\n",
        "    }\n\n    public static int clamp(int value, int min, int max) {\n" + CLAMP[variant] + "    }\n}\n")


def candidate(workspace: Path, root: Path, variant: str, extra: dict = None) -> Path:
    """A candidate worktree of ``workspace`` with clamp ``variant`` (None: no clamp) and ``extra`` files."""
    _git(workspace, "worktree", "add", "-q", "--detach", str(root))
    if variant is not None:
        (root / TARGET).write_text(calc_with_clamp(variant))
    for rel, text in (extra or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def base_revision(workspace: Path) -> str:
    return _git(workspace, "rev-parse", "HEAD").strip()
