"""P2 reproducer fixtures: the R2 T4 shape (commons-lang ArrayFill, run
20261005T195809-6a6add6d; handover/LR_R1_POST_P5_LIVE_SUCCESS_VALIDATION_R2.md).

- The workspace: ``ArrayFill.java`` with the existing ``fill(char[], int, int,
  char)`` overload, and ``ArrayFillTest.java`` whose existing test calls
  ``ArrayFill.fill(null, 0, 0, 'Z')`` at line 167, column-for-column as in
  commons-lang at the run's base 4ee346e59e.
- The plan: the live run's approved plan (s1 ArrayFill.java provides
  ``array_fill_int_range_method``; s2 ArrayFillTest.java requires it).
- The Maven test gate: Kriya's real ``run_tests`` / Maven adapter / FS-1A
  report binding run unchanged; only the ``mvn`` process
  (``PolymorphicValidator._run_maven_cmd``) is replaced by a deterministic
  oracle of what javac + Surefire do with these two files - its rule
  (an ``int[]`` overload beside the ``char[]`` one makes the uncast ``null``
  call ambiguous; a ``(char[])`` cast resolves it) is proven with real javac
  by ``test_p2_t4_shape_is_what_javac_reports``. On ambiguity it returns the
  exact measured T4 Maven output (tests/fixtures/p2/t4_full_regression_output.txt,
  sha256 e8bafb06...) and writes no Surefire report, as a test-compile failure
  writes none.
"""
import re
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "p2"
T4_OUTPUT = (FIXTURES / "t4_full_regression_output.txt").read_text()

MAIN = "src/main/java/org/apache/commons/lang3/ArrayFill.java"
TEST = "src/test/java/org/apache/commons/lang3/ArrayFillTest.java"
GOAL = ("Add `public static int[] fill(final int[] a, final int fromIndex, final int toIndex, final int val)` to "
        "org.apache.commons.lang3.ArrayFill, mirroring the existing `fill(char[], int, int, char)` overload (fill the "
        "range with `Arrays.fill`, return the given array, and return null for a null array), and add unit tests for "
        "it in ArrayFillTest.")

LICENSE = ("/*\n * Licensed to the Apache Software Foundation (ASF) under one or more\n"
           " * contributor license agreements.  See the NOTICE file distributed with\n"
           " * this work for additional information regarding copyright ownership.\n"
           " * The ASF licenses this file to You under the Apache License, Version 2.0\n"
           " * (the \"License\"); you may not use this file except in compliance with\n"
           " * the License.  You may obtain a copy of the License at\n *\n"
           " *      https://www.apache.org/licenses/LICENSE-2.0\n *\n"
           " * Unless required by applicable law or agreed to in writing, software\n"
           " * distributed under the License is distributed on an \"AS IS\" BASIS,\n"
           " * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n"
           " * See the License for the specific language governing permissions and\n"
           " * limitations under the License.\n */\n\n")

CHAR_OVERLOAD = (
    "    public static char[] fill(final char[] a, final int fromIndex, final int toIndex, final char val) {\n"
    "        if (a != null) {\n"
    "            Arrays.fill(a, fromIndex, toIndex, val);\n"
    "        }\n"
    "        return a;\n"
    "    }\n"
)
INT_OVERLOAD = (
    "\n    public static int[] fill(final int[] a, final int fromIndex, final int toIndex, final int val) {\n"
    "        if (a != null) {\n"
    "            Arrays.fill(a, fromIndex, toIndex, val);\n"
    "        }\n"
    "        return a;\n"
    "    }\n"
)
BASE_MAIN = (LICENSE + "package org.apache.commons.lang3;\n\nimport java.util.Arrays;\n\n"
             "/**\n * Fills and returns arrays in the fluent style.\n */\npublic final class ArrayFill {\n\n"
             + CHAR_OVERLOAD + "\n    private ArrayFill() {\n    }\n}\n")

AMBIGUOUS_CALL = "        final char[] actual = ArrayFill.fill(null, 0, 0, 'Z');"
CAST_CALL = "        final char[] actual = ArrayFill.fill((char[]) null, 0, 0, 'Z');"
CALL_LINE = 167


def _base_test() -> str:
    head = (LICENSE + "package org.apache.commons.lang3;\n\n"
            "import static org.junit.jupiter.api.Assertions.assertArrayEquals;\n"
            "import static org.junit.jupiter.api.Assertions.assertNull;\n\n"
            "import org.junit.jupiter.api.Test;\n\n"
            "/**\n * Tests {@link ArrayFill}.\n */\nclass ArrayFillTest {\n")
    lines = head.splitlines()
    index = 0
    while len(lines) + 5 <= CALL_LINE - 4:         # existing range tests, 5 lines each
        lines += ["", "    @Test", f"    void testFillCharArrayRange{index}() {{",
                  "        assertArrayEquals(new char[] {'Z'}, ArrayFill.fill(new char[1], 0, 1, 'Z'));", "    }"]
        index += 1
    while len(lines) < CALL_LINE - 3:
        lines.append("")
    lines += ["    @Test", "    void testFillCharArrayRangeNull() {", AMBIGUOUS_CALL, "        assertNull(actual);",
              "    }", "}"]
    text = "\n".join(lines) + "\n"
    assert text.splitlines()[CALL_LINE - 1] == AMBIGUOUS_CALL, "the call must sit at line 167, as at the T4 base"
    return text


BASE_TEST = _base_test()
POM = ("<project xmlns=\"http://maven.apache.org/POM/4.0.0\"><modelVersion>4.0.0</modelVersion>"
       "<groupId>org.apache.commons</groupId><artifactId>commons-lang3</artifactId>"
       "<version>3.21.1-SNAPSHOT</version></project>\n")
FILES = {"pom.xml": POM, ".gitignore": "target/\n", MAIN: BASE_MAIN, TEST: BASE_TEST}


def t4_plan():
    """The live T4 run's approved structured plan (plans/20261005T195809-6a6add6d.json): its
    subtasks, files, capabilities, dependencies and verification."""
    from kriya.workflow.plan_schema import EngineeringPlan

    return EngineeringPlan.model_validate({
        "plan_id": "p2-t4", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        # Acceptance criteria are omitted (their subtask mapping is not material to P2).
        "acceptance_criteria": [],
        "subtasks": [
            {"id": "s1", "description": "Add the public static int[] fill(int[], int, int, int) method to ArrayFill.java",
             "execution_method": "model", "relevant_global_invariant_ids": ["gi1"],
             "planned_files": [{"path": MAIN, "action": "modify", "environment_requirements": ["java"]}],
             "provides": ["array_fill_int_range_method"],
             "verification": [{"type": "tool", "tool_name": "compile", "verifier_kind": "compile",
                               "description": "Compile ArrayFill.java to ensure syntax and type correctness"}]},
            {"id": "s2", "description": "Add unit tests for the new fill method in ArrayFillTest.java",
             "execution_method": "model", "relevant_global_invariant_ids": ["gi1"],
             "planned_files": [{"path": TEST, "action": "modify", "environment_requirements": ["java", "junit"],
                                "requires_capabilities": ["array_fill_int_range_method"]}],
             "provides": ["array_fill_int_range_tests"], "requires": ["array_fill_int_range_method"],
             "depends_on": ["s1"],
             "verification": [{"type": "tool", "tool_name": "test", "verifier_kind": "test",
                               "requires_runtime_execution": True,
                               "description": "Run unit tests in ArrayFillTest.java to verify new functionality"}]},
        ]})


# ---------------------------------------------------------------- the mvn oracle

GREEN = ("[INFO] --- compiler:3.16.0:testCompile (default-testCompile) @ commons-lang3 ---\n"
         "[INFO] Compiling 1 source file with javac [debug target 1.8] to target/test-classes\n"
         "[INFO] -------------------------------------------------------\n[INFO]  T E S T S\n"
         "[INFO] -------------------------------------------------------\n"
         "[INFO] Running org.apache.commons.lang3.ArrayFillTest\n"
         "[INFO] Tests run: {n}, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.05 s -- in "
         "org.apache.commons.lang3.ArrayFillTest\n[INFO] \n[INFO] Results:\n[INFO] \n"
         "[INFO] Tests run: {n}, Failures: 0, Errors: 0, Skipped: 0\n[INFO] \n"
         "[INFO] ------------------------------------------------------------------------\n"
         "[INFO] BUILD SUCCESS\n[INFO] ------------------------------------------------------------------------\n")


def ambiguous(main_text: str, test_text: str) -> bool:
    """javac's verdict for these files (pinned by real javac): both array
    overloads present, and the uncast null call still there."""
    has_int = re.search(r"fill\(\s*final\s+int\[\]\s+a\s*,\s*final\s+int\s+fromIndex", main_text) is not None
    return has_int and AMBIGUOUS_CALL.strip() in test_text


def _surefire_xml(test_text: str) -> tuple:
    names = re.findall(r"@Test\s+(?:public\s+)?void\s+(\w+)\s*\(", test_text)
    cases = "".join(f'<testcase name="{n}" classname="org.apache.commons.lang3.ArrayFillTest" time="0"/>'
                    for n in names)
    return (f'<?xml version="1.0" encoding="UTF-8"?><testsuite name="org.apache.commons.lang3.ArrayFillTest" '
            f'tests="{len(names)}" errors="0" skipped="0" failures="0">{cases}</testsuite>'), len(names)


class MvnOracle:
    """Replaces ``PolymorphicValidator._run_maven_cmd`` (the mvn process
    only); records every invocation."""

    def __init__(self):
        self.calls = []

    def __call__(self, validator, goals, cwd, timeout=300, stdin_payload=None, deadline=None, tooling_only=False):
        root = Path(cwd)
        main_text, test_text = (root / MAIN).read_text(), (root / TEST).read_text()
        verdict = "ambiguous" if ambiguous(main_text, test_text) else "green"
        self.calls.append({"goals": list(goals), "cwd": str(cwd), "verdict": verdict})
        if goals[:2] == ["clean", "compile"]:
            classes = root / "target" / "classes" / "org" / "apache" / "commons" / "lang3"
            classes.mkdir(parents=True, exist_ok=True)
            (classes / "ArrayFill.class").write_bytes(b"\xca\xfe\xba\xbe")
            return {"returncode": 0, "stdout": "[INFO] BUILD SUCCESS\n", "stderr": "", "timed_out": False}
        assert goals[0] == "test", goals
        if verdict == "ambiguous":
            return {"returncode": 1, "stdout": T4_OUTPUT, "stderr": "", "timed_out": False}
        xml, count = _surefire_xml(test_text)
        reports = root / "target" / "surefire-reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "TEST-org.apache.commons.lang3.ArrayFillTest.xml").write_text(xml)
        return {"returncode": 0, "stdout": GREEN.format(n=count), "stderr": "", "timed_out": False}


INT_TEST = ("\n    @Test\n    void testFillIntArrayRange() {\n"
            "        assertArrayEquals(new int[] {7, 7}, ArrayFill.fill(new int[2], 0, 2, 7));\n    }\n")


def repaired_test() -> str:
    return BASE_TEST.replace(AMBIGUOUS_CALL, CAST_CALL).rstrip("\n").rstrip("}") + INT_TEST.lstrip("\n").join(
        ["\n", "}\n"])


def responder(role, request):
    """s1: the live candidate (the int[] overload, an anchored edit after the
    char[] overload). ArrayFillTest.java, whenever a request may write it:
    cast the null call and add the int[] tests."""
    from _chaos_harness import benign_roles
    from _protocol_responses import sentinel

    system = next((m["content"] for m in request.messages if m["role"] == "system"), "")
    user = next((m["content"] for m in reversed(request.messages) if m["role"] == "user"), "")
    test_request = f'path="{TEST}"' in system
    if role == "file_list":
        return '{"files": ["%s"]}' % (TEST if TEST in user.split("ONLY")[-1] and MAIN not in user.split("ONLY")[-1]
                                      else MAIN)
    if role == "developer":
        whole_file = '<<<KRIYA:EDIT path=' not in system   # answer in the operation the contract offers
        if test_request and whole_file:
            return sentinel(TEST, analysis="cast the null char[] call; add int[] tests.",
                            content=repaired_test())
        if test_request:
            return sentinel(TEST, analysis="cast the null char[] call; add int[] tests.",
                            edits=[(AMBIGUOUS_CALL + "\n        assertNull(actual);\n    }",
                                    CAST_CALL + "\n        assertNull(actual);\n    }\n" + INT_TEST.rstrip("\n"))])
        if whole_file:
            return sentinel(MAIN, analysis="add the int[] range overload.",
                            content=BASE_MAIN.replace(CHAR_OVERLOAD, CHAR_OVERLOAD + INT_OVERLOAD))
        return sentinel(MAIN, analysis="add the int[] range overload.",
                        edits=[(CHAR_OVERLOAD.rstrip("\n"), (CHAR_OVERLOAD + INT_OVERLOAD).rstrip("\n"))])
    return benign_roles(role, request, target=MAIN)


def run(tmp_path, monkeypatch, oracle=None, **autonomy):
    from _chaos_harness import chaos_config
    from _t6_harness import enforce_run

    from kriya.tools.validate import PolymorphicValidator

    oracle = oracle or MvnOracle()
    cfg = chaos_config(**{"brownfield_full_regression_baseline_policy": "required", **autonomy})
    monkeypatch.setattr(PolymorphicValidator, "_run_maven_cmd",
                        lambda self, goals, cwd, *a, **k: oracle(self, goals, cwd, *a, **k))
    observed = enforce_run(tmp_path, monkeypatch, responder, FILES, GOAL, [t4_plan] * 4, cfg=cfg)
    observed.extra["oracle"] = oracle
    return observed


def blob_json(observed, record, name):
    import json

    digest = (record.get("blobs") or {}).get(name)
    return json.loads(observed.run.blob(digest)) if digest else None


def blob_text(observed, record, name):
    digest = (record.get("blobs") or {}).get(name)
    return observed.run.blob(digest).decode() if digest else None


def workspace_text(observed, path):
    return (observed.workspace / path).read_text()

