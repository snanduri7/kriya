"""HOSTED-KUP-WRITE-BOUNDARY-PORTABLE-001: the explicit NOT_APPLICABLE_ON_<platform> disposition (``not_applicable_on``), not an allowlist.

A test whose MECHANISM exists only off one platform (macOS sandbox-exec) is NOT APPLICABLE there when the same INVARIANT is proved by a named test: the skip is recorded with
its equivalent, it never fails the certification as an unexpected skip, and the execution audit requires that equivalent to be in the census and to have PASSED. Anything
else - another platform, a missing equivalent, a failed or absent equivalent - stays a failure.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "certification" / "sample_not_applicable.py"
KUP = ROOT / "tests" / "test_kup_write_boundary.py"


def _run(tmp_path, *args):
    report = tmp_path / "cert"
    completed = subprocess.run([sys.executable, "-m", "pytest", str(SAMPLE), "-p", "no:cacheprovider", "-q", "--certification", "--certification-report", str(report), *args],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=300, env={**os.environ, "KRIYA_CERTIFICATION": "", "TMPDIR": str(tmp_path)})
    return completed, json.loads((report / "skips.json").read_text())


def test_a_skip_declared_not_applicable_here_with_a_named_equivalent_is_recorded_and_not_unexpected(tmp_path):
    completed, skips = _run(tmp_path, "-k", "mechanism_of_another_platform or invariant_is_proved_here")
    assert completed.returncode == 0, completed.stdout[-1500:]
    assert skips["unexpected"] == 0 and skips["skipped"] == 1
    assert [(n["nodeid"].split("::")[-1], n["equivalent"].split("::")[-1]) for n in skips["not_applicable"]] == [("test_mechanism_of_another_platform", "test_the_invariant_is_proved_here")]
    assert skips["skips"][0]["allowlisted"] is None, "not applicable is NOT an allowlist entry"


def test_a_declaration_for_another_platform_or_without_an_equivalent_excuses_nothing(tmp_path):
    other, skips_other = _run(tmp_path / "a", "-k", "declared_for_another_platform_only")
    assert other.returncode == 1 and skips_other["unexpected"] == 1 and skips_other["not_applicable"] == []
    assert "UNEXPECTED SKIP in certification mode" in other.stdout
    bare, skips_bare = _run(tmp_path / "b", "-k", "declaration_without_an_equivalent")
    assert bare.returncode == 1 and skips_bare["unexpected"] == 1 and skips_bare["not_applicable"] == []


def _census(*records):
    return {"tests": [{"id": i, "markers": [], "skip_reasons": [], "docker": False, "requires_platform": None, "not_applicable_on": n} for i, n in records]}


def test_the_execution_audit_requires_the_named_equivalent_to_exist_and_to_have_passed():
    from _certification_collection import _junit_key, executed

    mechanism, proof = "t.py::test_macos_only", "t.py::test_portable"
    declared = {"platform": "linux", "equivalent": proof}
    census = _census((mechanism, declared), (proof, None))

    def outcomes(status):
        return {_junit_key(mechanism): "skipped", _junit_key(proof): status}
    ok = executed(census, outcomes("passed"), floors={}, platform="linux")
    assert ok["ok"] and ok["not_applicable"] == [{"id": mechanism, "platform": "linux", "equivalent": proof, "equivalent_outcome": "passed"}]
    for status in ("failed", "skipped"):
        bad = executed(census, outcomes(status), floors={}, platform="linux")
        assert not bad["ok"] and any(f"its equivalent {proof} is {status}" in p for p in bad["problems"])
    assert not executed(census, {_junit_key(mechanism): "skipped"}, floors={}, platform="linux")["ok"], "an equivalent that did not run at all"
    dangling = executed(_census((mechanism, declared)), {_junit_key(mechanism): "skipped"}, floors={}, platform="linux")
    assert not dangling["ok"] and any("not in the census" in p for p in dangling["problems"]), "an equivalent that is not even collected"
    on_mac = executed(census, outcomes("failed"), floors={}, platform="darwin")
    assert on_mac["ok"] and on_mac["not_applicable"] == [], "the declaration applies on linux only"


def test_the_two_macos_sandbox_tests_name_the_portable_linux_proofs_that_exist():
    import _certification_collection as collection
    from _certification import declared_not_applicable

    source = KUP.read_text(encoding="utf-8")
    portable = (ROOT / "tests" / "test_kup_write_boundary_portable.py").read_text(encoding="utf-8")
    marks = [pytest.mark.not_applicable_on(*args, **kwargs).mark for args, kwargs in [(("linux",), {"equivalent": "x::y"})]]
    assert collection.census_record("a::b", marks, False)["not_applicable_on"] == {"platform": "linux", "equivalent": "x::y"}
    assert declared_not_applicable([]) is None
    named = [line.split('equivalent="')[1].rstrip('")\n') for line in source.splitlines() if line.startswith("@pytest.mark.not_applicable_on(")]
    assert len(named) == 2
    for node in named:
        path, name = node.split("::")
        assert path == "tests/test_kup_write_boundary_portable.py" and f"def {name}(" in portable, node
