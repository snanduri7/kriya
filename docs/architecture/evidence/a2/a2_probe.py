"""Slice A2 probe (model-free, read-only against the runtime package).

Question 1 (P1-P7): does every run-authored, executed test become a generated-verifier subject of
``kriya.workflow.verifier_semantic.derive_executed_verifiers``? The report identities are produced by REAL pytest runs
(JUnit XML parsed by Kriya's own ``kriya.tools.test_execution.parse_junit_xml``), so the identity format is measured,
not assumed. A case that executed, failed, was written by the run and yields no subject and no "undetermined" flag
is outside the semantic interception: the unit's historical retry path handles it.

Question 2 (P8-P10): what does the sealed verification contract hold for a general behaviour goal, and what does
admission say? Compiled by the real ``requirements`` / ``contract_compilation`` owners, no model.

Run: <venv python> docs/architecture/evidence/a2/a2_probe.py   (PYTHONPATH = the closure worktree)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from kriya.tools.test_execution import parse_junit_xml
from kriya.workflow.verifier_semantic import derive_executed_verifiers

FAILING_FUNCTION = b"def test_counts():\n    assert 1 == 2\n"
FAILING_CLASS = b"class TestWidget:\n    def test_counts(self):\n        assert 1 == 2\n"
FAILING_PARAM = b"import pytest\n\n@pytest.mark.parametrize('n', [1, 2])\ndef test_counts(n):\n    assert n == 0\n"
FAILING_UNITTEST = b"import unittest\n\nclass WidgetCase(unittest.TestCase):\n    def test_counts(self):\n        self.assertEqual(1, 2)\n"


def run_pytest(files: dict, target: str, extra: tuple = ()) -> dict:
    """Write ``files`` into a fresh directory, run real pytest on ``target``, return Kriya's report dict shape."""
    with tempfile.TemporaryDirectory() as root:
        for path, data in files.items():
            full = os.path.join(root, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as handle:
                handle.write(data)
        report_path = os.path.join(root, "junit.xml")
        proc = subprocess.run(
            [sys.executable, "-I", "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report_path}", *extra, target],
            cwd=root, capture_output=True, text=True, timeout=120, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        with open(report_path, "rb") as handle:
            cases = parse_junit_xml(handle.read())
    return {"completeness": "COMPLETE", "runner": "pytest", "exit_code": proc.returncode,
            "test_cases": [{"identity": c.identity, "classname": c.classname, "name": c.name, "status": c.status} for c in cases]}


def show(label: str, report: dict, authored: dict) -> None:
    derivation = derive_executed_verifiers(report, authored)
    print(f"{label}")
    print(f"  executed identities : {sorted({c['identity'] for c in report['test_cases']})}")
    print(f"  failed identities   : {sorted({c['identity'] for c in report['test_cases'] if c['status'] in ('failed', 'error')})}")
    print(f"  authored paths      : {sorted(authored)}")
    print(f"  subjects            : {[(v.path, list(v.failed_identities)) for v in derivation.verifiers]}")
    print(f"  undetermined        : {derivation.undetermined} (ambiguous={list(derivation.ambiguous)}, unreadable={list(derivation.unreadable)})")
    intercepted = bool(derivation.failing) or derivation.undetermined
    print(f"  RESULT              : {'INTERCEPTED (subject or typed unknown)' if intercepted else 'NOT INTERCEPTED (historical retry path)'}")
    print()


def question_one() -> None:
    print("=== Question 1: subject derivation over real pytest identities ===\n")
    files = {"tests/test_widget.py": FAILING_FUNCTION}
    show("P1 module-level test in tests/ (no __init__)", run_pytest(files, "tests"), files)

    files = {"tests/test_widget.py": FAILING_CLASS}
    show("P2 test method of a class in tests/", run_pytest(files, "tests"), files)

    files = {"tests/test_widget.py": FAILING_PARAM}
    show("P3 parameterized test in tests/", run_pytest(files, "tests"), files)

    files = {"tests/test_widget.py": FAILING_UNITTEST}
    show("P4 unittest.TestCase method in tests/", run_pytest(files, "tests"), files)

    files = {"src/pkg/__init__.py": b"", "src/pkg/test_widget.py": FAILING_FUNCTION}
    show("P5 test under a src/ layout root (src/pkg/test_widget.py, rootdir-relative run)", run_pytest(files, "src"), files)

    files = {"pkg/__init__.py": b"", "pkg/tests/__init__.py": b"", "pkg/tests/test_widget.py": FAILING_FUNCTION}
    show("P6 test inside a package with __init__ files", run_pytest(files, "pkg"), files)

    # P7: a run-authored test in a language the structural parser has no grammar for. The report identity is
    # hand-built in the documented classname.name form (no JS/Kotlin runner is started here): the point measured
    # is only what the derivation does with an authored path it cannot parse.
    report = {"completeness": "COMPLETE", "runner": "gradle",
              "test_cases": [{"identity": "com.example.WidgetTest.counts", "classname": "com.example.WidgetTest", "name": "counts", "status": "failed"}]}
    authored = {"src/test/kotlin/com/example/WidgetTest.kt": b"package com.example\nclass WidgetTest {\n  @Test fun counts() { assert(false) }\n}\n"}
    show("P7 run-authored Kotlin test (no structural grammar), JUnit identity failed", report, authored)


def question_two() -> None:
    from kriya.workflow.contract_compilation import compile_verification_contract
    from kriya.workflow.requirements import derive_requirements

    print("=== Question 2: what the sealed verification contract holds ===\n")
    goals = {
        "P8 general behaviour, no operator authority":
            "Add a function percent(part, whole) that returns part as a percentage of whole rounded to one decimal place. "
            "Include a pytest test for it.",
        "P9 binary field semantics (run-10 shape), no operator authority":
            "Add an encoder that writes the record header: a 4-byte big-endian time field followed by a 2-byte length. "
            "Add a decoder that reads it back. Add tests for the round trip.",
        "P10 runtime-observable grammar":
            "Create hello.py. It prints hello world when executed.",
    }
    for label, goal in goals.items():
        requirement_set = derive_requirements(goal)
        contract = compile_verification_contract(requirement_set, project_language="python")
        report = contract.report()
        refusal = contract.refusal()
        print(label)
        print(f"  goal      : {goal}")
        print(f"  admission : {report['admission']}  totals={json.dumps(report['totals'], sort_keys=True)}")
        print(f"  refusal   : {type(refusal).__name__ if refusal is not None else None}")
        for row in report["requirements"]:
            residual = [(r["claim"], r.get("strength")) for r in row["residual"]]
            print(f"  {row['id']}: status={row['status']} claims={row['claims']} strength={row['strength']} closers={row['closers']} residual={residual}")
            print(f"        text={row['text']!r}")
        entry_keys = sorted(contract.entries[0].to_dict()) if contract.entries else []
        print(f"  fields of one sealed entry: {entry_keys}")
        print()


if __name__ == "__main__":
    question_one()
    question_two()
