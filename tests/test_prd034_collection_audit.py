"""PRD-034: the collection/deselection audit and the tier-execution check (tests/_certification_collection.py).

The defect class (WAVE5-LIVE-MODEL-MARKED-MAVEN-TEST-001): a mandatory test that carries the wrong tier marker silently leaves the canonical population, and
zero skipped tests then proves nothing. These tests reproduce that mechanism and pin each rule with a negative control.
"""
import copy
import os
import subprocess
import sys

import _certification_collection as cc
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rec(nodeid, *markers, docker=False, reasons=()):
    return {"id": nodeid, "markers": sorted(markers), "skip_reasons": list(reasons), "docker": docker}


def census(records, selection=""):
    return {"selection": selection, "args": [], "count": len(records), "tests": records}


FLOORS = {"real_toolchain": 1, "live_static_analysis": 1, "docker": 1, "jvm": 1, "gradle_container": 1}


def population():
    return [
        rec("t/a.py::test_plain"),
        rec("t/m.py::test_real_maven", "real_toolchain", "skipif", reasons=["needs maven"]),
        rec("t/s.py::test_scanner", "live_static_analysis"),
        rec("t/g.py::test_gradle_container", "skipif", docker=True, reasons=["docker daemon not reachable"]),
        rec("t/l.py::test_live", "live_model"),
    ]


def selected_of(records):
    return census([r for r in records if "live_model" not in r["markers"]], selection="not live_model")


def test_the_intended_population_passes_and_reports_exactly_the_live_model_deselection():
    records = population()
    report = cc.audit(census(records), selected_of(records), FLOORS)
    assert report["ok"], report["problems"]
    assert report["deselected_ids"] == ["t/l.py::test_live"] and (report["selected"], report["deselected"], report["total"]) == (4, 1, 5)
    assert report["tiers_selected"] == {"docker": 1, "gradle_container": 1, "jvm": 1, "live_static_analysis": 1, "real_toolchain": 1}


def test_a_real_toolchain_test_carrying_the_live_model_marker_is_a_failure_even_with_zero_skips():
    """The WAVE5 mechanism: the real-Maven test is marked live_model, so the canonical selection drops it and no skip is ever reported."""
    records = population()
    records[1] = rec("t/m.py::test_real_maven", "live_model", "real_toolchain", "skipif", reasons=["needs maven"])
    report = cc.audit(census(records), selected_of(records), FLOORS)
    assert not report["ok"]
    assert any("real_toolchain test missing from the canonical population: t/m.py::test_real_maven" in p for p in report["problems"])
    assert any("below" in p or "floor" in p for p in report["problems"])   # and the tier's floor is breached as well


def test_a_deselected_test_that_is_not_live_model_is_a_failure():
    records = population()
    chosen = census([r for r in records if r["id"] != "t/a.py::test_plain" and "live_model" not in r["markers"]], selection="not live_model")
    report = cc.audit(census(records), chosen, FLOORS)
    assert not report["ok"] and "DESELECTED but not live_model: t/a.py::test_plain" in report["problems"]


def test_a_vanished_tier_below_its_floor_is_a_failure():
    records = [r for r in population() if r["id"] != "t/s.py::test_scanner"]
    report = cc.audit(census(records), selected_of(records), FLOORS)
    assert not report["ok"] and any("live_static_analysis" in p and "floor" in p for p in report["problems"])


@pytest.mark.parametrize("selection", ["", "not live_model and not real_toolchain", "not live_model or real_toolchain"])
def test_the_selection_must_be_exactly_the_canonical_expression(selection):
    records = population()
    chosen = selected_of(records)
    chosen["selection"] = selection
    assert not cc.audit(census(records), chosen, FLOORS)["ok"]


def test_a_live_model_test_in_the_canonical_run_and_a_filtered_baseline_are_failures():
    records = population()
    everything = census(records, selection="")
    leaky = copy.deepcopy(everything)
    leaky["selection"] = "not live_model"
    assert any("live_model test selected" in p for p in cc.audit(everything, leaky, FLOORS)["problems"])
    filtered = census(records, selection="real_toolchain")
    assert any("itself filtered" in p for p in cc.audit(filtered, selected_of(records), FLOORS)["problems"])


def test_the_repository_floors_are_what_the_audit_enforces_by_default():
    records = population()
    assert not cc.audit(census(records), selected_of(records))["ok"]   # the toy population is far below the measured floors
    assert cc.MANDATORY_TIER_FLOORS["real_toolchain"] >= 5 and cc.MANDATORY_TIER_FLOORS["live_static_analysis"] >= 11


def test_the_junit_key_maps_node_ids_for_module_class_and_parametrized_tests():
    assert cc._junit_key("tests/test_a.py::test_x") == ("tests.test_a", "test_x")                       # noqa: SLF001
    assert cc._junit_key("tests/architecture/test_b.py::TestC::test_y[a::b-1]") == ("tests.architecture.test_b.TestC", "test_y[a::b-1]")   # noqa: SLF001


def _junit(tmp_path, cases):
    body = "".join(f'<testcase classname="{c}" name="{n}">{extra}</testcase>' for c, n, extra in cases)
    path = tmp_path / "junit.xml"
    path.write_text(f'<?xml version="1.0"?><testsuites><testsuite tests="{len(cases)}">{body}</testsuite></testsuites>', encoding="utf-8")
    return str(path)


def test_tier_execution_requires_every_mandatory_test_to_have_passed_not_merely_to_be_unskipped(tmp_path):
    records = population()
    chosen = selected_of(records)
    base = [("t.a", "test_plain", ""), ("t.m", "test_real_maven", ""), ("t.s", "test_scanner", ""), ("t.g", "test_gradle_container", "")]
    passed = cc.executed(chosen, cc.junit_outcomes(_junit(tmp_path, base)), FLOORS)
    assert passed["ok"], passed["problems"]
    skipped = [(c, n, '<skipped message="x"/>' if n == "test_real_maven" else extra) for c, n, extra in base]
    assert not cc.executed(chosen, cc.junit_outcomes(_junit(tmp_path, skipped)), FLOORS)["ok"]
    failed = [(c, n, "<failure/>" if n == "test_scanner" else extra) for c, n, extra in base]
    assert not cc.executed(chosen, cc.junit_outcomes(_junit(tmp_path, failed)), FLOORS)["ok"]
    missing = [case for case in base if case[1] != "test_gradle_container"]
    report = cc.executed(chosen, cc.junit_outcomes(_junit(tmp_path, missing)), FLOORS)
    assert not report["ok"] and report["per_tier"]["gradle_container"]["missing"] == 1


def test_the_real_collection_census_is_written_and_audited_end_to_end(tmp_path):
    """The conftest option writes the census of a real (tiny) collection; the CLI audits it. A real collection, not a hand-built dict."""
    out = tmp_path / "census.json"
    result = subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "--collect-only", "-p", "no:cacheprovider",
                             f"--collection-census={out}", "tests/test_prd034_certification.py"],
                            cwd=REPO, capture_output=True, text=True, check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stdout[-400:] + result.stderr[-400:]
    census_payload = cc._load(str(out))                                                              # noqa: SLF001
    assert census_payload["count"] > 5 and census_payload["selection"] == ""
    assert all({"id", "markers", "skip_reasons", "docker"} <= set(r) for r in census_payload["tests"])
