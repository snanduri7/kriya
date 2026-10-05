"""P2 reproducer: R2 T4 - a candidate-caused test-compilation failure in the
full regression is reported REGRESSION_UNATTRIBUTED and never repaired
(handover/P2_REGRESSION_ATTRIBUTION_INVESTIGATION.md).

``test_p2_t4_shape_is_what_javac_reports`` grounds the fixtures in real javac.
The required behaviour (handover/evidence/p2/test_p2_required_behaviour.py,
failing before the fix) is asserted here end to end; the pre-fix pin is kept
as evidence (handover/evidence/p2/test_p2_prefix_pin.py).
"""
import shutil
import subprocess

import _p2_t4_shape as t4
import pytest

from kriya.workflow.attribution import DETERMINISTIC_ATTRIBUTION_TIERS


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


def test_a_candidate_caused_test_compile_regression_is_attributed_and_repaired(tmp_path, monkeypatch):
    observed = t4.run(tmp_path, monkeypatch)
    decisions = [r["payload"] for r in observed.of("recovery.decision")]

    # 1. Never the unattributed stop.
    assert all(d.get("stop_reason_code") != "REGRESSION_UNATTRIBUTED" for d in decisions)
    assert all(r["payload"]["type"] != "regression_unattributed" for r in observed.of("mirror.gate_outcome"))

    # 2. The regression is grounded deterministically to the compiler's locator.
    regressions = [r["payload"] for r in observed.of("diagnosis") if r["payload"]["type"] == "regression_test"]
    assert regressions, "the candidate-caused full-regression failure must be an ordinary, attributable regression"
    first = regressions[0]
    assert {"filepath": t4.TEST, "line": 167} in [{k: loc[k] for k in ("filepath", "line")}
                                                   for loc in first["file_locations"]]
    assert first["attribution_tier"] in DETERMINISTIC_ATTRIBUTION_TIERS

    # 3. An authorized repair route: a later Developer request may write the broken test file.
    assert any(t4.TEST in (r["payload"].get("authorized_write_scope") or []) for r in observed.of("authority.snapshot"))

    # 4. End to end: the repair is applied and the run succeeds on a green full regression.
    assert observed.result.legacy_result["status"] == "success"
    assert t4.CAST_CALL in t4.workspace_text(observed, t4.TEST)
    assert t4.INT_OVERLOAD.strip() in t4.workspace_text(observed, t4.MAIN)
    assert observed.extra["oracle"].calls[-1]["verdict"] == "green"


def test_p2_the_attribution_names_the_compiler_locator_and_the_carried_locus_is_revision_bound(
        tmp_path, monkeypatch):
    """The chain's intermediate facts: the attribution event names exactly
    the error locator (the COMPILATION WARNING locator in the same output,
    CharSetTest.java:[394,55], is not an error and is ignored); the
    PLAN_SCOPE_REVISION_REQUIRED decision requires exactly ArrayFillTest.java;
    the re-invocation seeds line 167 because the file's revision is unchanged."""
    observed = t4.run(tmp_path, monkeypatch)
    events = [t4.blob_json(observed, r, "event") for r in observed.of("mirror.event")
              if r["payload"]["kind"] in ("validation_baseline.compile_regression_attributed",
                                          "recovery.grounded_loci")]
    [attributed] = [e for e in events if e.get("kind") == "validation_baseline.compile_regression_attributed"]
    assert attributed["details"] == {"files": [t4.TEST], "locations": [{"filepath": t4.TEST, "line": 167}]}
    conflicts = [r["payload"]["plan_scope_conflict"] for r in observed.of("recovery.decision")
                 if r["payload"].get("plan_scope_conflict")]
    assert conflicts[0] == {"required_files": [t4.TEST], "reason_code": "PLAN_SCOPE_REVISION_REQUIRED"}
    [carried] = [e for e in events if e.get("kind") == "recovery.grounded_loci"]
    assert carried["details"] == {"seeded": [{"filepath": t4.TEST, "line": 167}], "dropped": []}
