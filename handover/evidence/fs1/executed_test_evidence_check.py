"""FS-1 design demonstration (evidence only; no production code): the deterministic check the proposed design
adds, applied to the real R2 artifacts. For a test file the candidate changed: the test methods it ADDED
(structural parse of base vs candidate with Kriya's own code-intel parser, annotation-aware) must each appear as an
executed testcase in the runner's own structured report (Surefire JUnit XML) of the candidate's gate run.
usage: python executed_test_evidence_check.py   (run with PYTHONPATH=<repo>)"""
import os
import xml.etree.ElementTree as ET

from kriya.code_intel.parsing import parse_text

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "specimens")


def methods(path, text):
    return {s.name: s for s in parse_text(path, text).symbols if s.kind == "method"}


def check(name, base, candidate, report, path):
    before = methods(path, open(os.path.join(HERE, base)).read())
    after = methods(path, open(os.path.join(HERE, candidate)).read())
    added = sorted(set(after) - set(before))
    suite = ET.parse(os.path.join(HERE, report)).getroot()
    executed = {case.get("name"): ("failed" if case.find("failure") is not None or case.find("error") is not None
                                   else "skipped" if case.find("skipped") is not None else "passed")
                for case in suite.iter("testcase")}
    rows = [(m, "@Test" in " ".join(after[m].annotations) or "Test" in after[m].annotations,
             executed.get(m, "NOT_EXECUTED")) for m in added]
    verdict = "SUFFICIENT" if rows and all(state == "passed" for _m, _a, state in rows) else "INSUFFICIENT"
    print(f"{name}: suite={suite.get('name')} executed={len(executed)} added_test_methods={rows} -> {verdict}")
    return verdict


if __name__ == "__main__":
    t3 = check("T3", "T3-CharSetUtilsTest.base.java", "T3-CharSetUtilsTest.candidate.java",
               "T3-TEST-CharSetUtilsTest.xml", "src/test/java/org/apache/commons/lang3/CharSetUtilsTest.java")
    t5 = check("T5", "T5-OwnerTests.base.java", "T5-OwnerTests.candidate.java", "T5-TEST-OwnerTests.xml",
               "src/test/java/org/springframework/samples/petclinic/model/OwnerTests.java")
    assert (t3, t5) == ("INSUFFICIENT", "SUFFICIENT")
