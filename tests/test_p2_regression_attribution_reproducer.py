"""P2 reproducer: R2 T4 - a candidate-caused test-compilation failure in the
full regression is reported REGRESSION_UNATTRIBUTED and never repaired
(handover/P2_REGRESSION_ATTRIBUTION_INVESTIGATION.md).

``test_p2_t4_shape_is_what_javac_reports`` grounds the fixtures in real javac.
``test_p2_today_*`` pins today's path (passes now; its assertions describe
the defect). The required behaviour is asserted by
handover/evidence/p2/test_p2_required_behaviour.py (run explicitly; it fails
until P2 is fixed, then moves here replacing the pin - no xfail).
"""
import shutil
import subprocess

import _p2_t4_shape as t4
import pytest


@pytest.mark.skipif(shutil.which("javac") is None, reason="needs a local javac")
def test_p2_t4_shape_is_what_javac_reports(tmp_path):
    """Real javac on the fixture files (JUnit replaced by two stubs): with the
    candidate's int[] overload the existing line-167 call is ambiguous with
    exactly T4's message; the base, and the cast repair, compile."""
    stubs = {
        "org/junit/jupiter/api/Test.java": "package org.junit.jupiter.api;\npublic @interface Test {}\n",
        "org/junit/jupiter/api/Assertions.java": (
            "package org.junit.jupiter.api;\npublic final class Assertions {\n"
            "  public static void assertNull(Object o) {}\n"
            "  public static void assertArrayEquals(char[] a, char[] b) {}\n"
            "  public static void assertArrayEquals(int[] a, int[] b) {}\n}\n"),
    }
    for rel, text in stubs.items():
        (tmp_path / "stubs" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "stubs" / rel).write_text(text)

    def compile_with(main_text, test_text):
        work = tmp_path / f"w{len(list(tmp_path.iterdir()))}"
        for rel, text in ((t4.MAIN, main_text), (t4.TEST, test_text)):
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text(text)
        sources = [str(work / t4.MAIN), str(work / t4.TEST)] + [str(p) for p in (tmp_path / "stubs").rglob("*.java")]
        proc = subprocess.run(["javac", "-d", str(work / "out"), *sources], capture_output=True, text=True,
                              check=False)
        return proc.returncode, proc.stdout + proc.stderr

    candidate_main = t4.BASE_MAIN.replace(t4.CHAR_OVERLOAD, t4.CHAR_OVERLOAD + t4.INT_OVERLOAD)
    assert compile_with(t4.BASE_MAIN, t4.BASE_TEST)[0] == 0
    code, output = compile_with(candidate_main, t4.BASE_TEST)
    assert code != 0
    assert "ArrayFillTest.java:167: error: reference to fill is ambiguous" in output
    assert ("both method fill(char[],int,int,char) in ArrayFill and method fill(int[],int,int,int) in ArrayFill "
            "match") in output
    assert t4.ambiguous(candidate_main, t4.BASE_TEST)                     # the oracle agrees
    repaired = t4.repaired_test()   # what the scripted repair writes: the cast, plus the int[] test
    assert compile_with(candidate_main, repaired)[0] == 0
    assert not t4.ambiguous(candidate_main, repaired) and not t4.ambiguous(t4.BASE_MAIN, t4.BASE_TEST)
    # The oracle's ambiguous output is the measured T4 output, byte for byte.
    assert "ArrayFillTest.java:[167,40] reference to fill is ambiguous" in t4.T4_OUTPUT


def test_p2_today_a_candidate_caused_test_compile_regression_is_unattributed_and_never_repaired(
        tmp_path, monkeypatch):
    """Pins today's path (it passes now; its assertions describe the defect)."""
    observed = t4.run(tmp_path, monkeypatch)
    legacy = observed.result.legacy_result
    oracle_calls = observed.extra["oracle"].calls

    # PRE baseline green; the candidate compiles; the full regression's test compile fails.
    tests_runs = [c for c in oracle_calls if c["goals"][0] == "test"]
    assert tests_runs[0]["verdict"] == "green" and tests_runs[-1]["verdict"] == "ambiguous"
    assert legacy["status"] != "success"
    [closed] = [r["payload"] for r in observed.of("unit.closed")]
    assert closed["failure_category"] == "regression_unattributed"
    # Kriya had the compiler's locator (file, line, the conflicting new method) ...
    [regression] = [r for r in observed.of("mirror.gate_outcome") if r["payload"]["type"] == "regression_unattributed"]
    outcome = t4.blob_json(observed, regression, "outcome")
    assert "ArrayFillTest.java:[167,40] reference to fill is ambiguous" in outcome["output"]
    assert "fill(int[],int,int,int) in org.apache.commons.lang3.ArrayFill" in outcome["output"]
    # ... but reported nothing attributable, stopped, and repaired nothing.
    assert outcome["file_locations"] == [] and outcome["likely_files"] == []
    [diagnosis] = [r["payload"] for r in observed.of("diagnosis") if r["payload"]["type"] == "regression_unattributed"]
    assert diagnosis["evidence_class"] == "UNKNOWN" and diagnosis["file_locations"] == []
    decisions = [r["payload"] for r in observed.of("recovery.decision")]
    assert decisions[-1]["action"] == "stop_environment"
    assert decisions[-1]["stop_reason_code"] == "REGRESSION_UNATTRIBUTED"
    assert not any(t4.TEST in (r["payload"].get("authorized_write_scope") or [])
                   for r in observed.of("authority.snapshot"))            # no request could repair the test
    assert [r["payload"]["unit_id"] for r in observed.of("unit.opened")] == ["s1"]   # s2 never ran
    assert t4.workspace_text(observed, t4.MAIN) == t4.BASE_MAIN           # nothing applied
    assert t4.workspace_text(observed, t4.TEST) == t4.BASE_TEST
