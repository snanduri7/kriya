"""D8 model-free reproducer: the terminal named-test closure (workflow.close_requirements_with_named_tests) on a
greenfield candidate that declares Java 17 through an authorized pom.xml, under the production containment setting
(contained_execution_required). KNOW B runtime-6 shape (no named tests), plus the same with one named test.
Usage: python d8_reproducer.py <repo root>"""
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT))
from kriya.config.config import AutonomyConfig  # noqa: E402
from kriya.tools.validate import PolymorphicValidator  # noqa: E402
from kriya.workflow.obligations import ObligationLedger  # noqa: E402
from kriya.workflow.requirements import (  # noqa: E402
    RequirementOutcome,
    derive_requirements,
    record_requirement_verdicts,
    seed_requirement_obligations,
)
from kriya.workflow.workflow import close_requirements_with_named_tests  # noqa: E402

POM = ('<project><modelVersion>4.0.0</modelVersion><groupId>d</groupId><artifactId>d</artifactId><version>1</version>'
       '<properties><maven.compiler.release>17</maven.compiler.release></properties></project>')
CFG = AutonomyConfig(contained_execution_required=True, containment_backend="oci")
report = []
for case, goal, named in (("no named tests (KNOW B shape)", "Create a Maven application targeting Java 17.", False),
                          ("one named test", "Create a Maven application targeting Java 17.\n- AppTest keeps passing\n",
                           True)):
    with tempfile.TemporaryDirectory() as tmp:
        ws, cand = os.path.join(tmp, "ws"), os.path.join(tmp, "cand")
        os.makedirs(ws)
        os.makedirs(os.path.join(cand, "src/main/java/demo"))
        Path(cand, "pom.xml").write_text(POM)
        Path(cand, "src/main/java/demo/App.java").write_text("package demo; public class App {}")
        if named:
            os.makedirs(os.path.join(cand, "src/test/java/demo"))
            Path(cand, "src/test/java/demo/AppTest.java").write_text("package demo; public class AppTest {}")
        reqs = derive_requirements(goal)
        ledger = ObligationLedger()
        seed_requirement_obligations(ledger, reqs)
        record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "") for r in reqs.requirements},
                                    revision=1, evidence_fingerprint="cand", source="reproducer")
        kwargs = {"toolchain_declaration_mutable": True} if "toolchain_declaration_mutable" in inspect.signature(
            close_requirements_with_named_tests).parameters else {}
        constructed, runs = [], []
        real_init = PolymorphicValidator.__init__

        def counting_init(self, *a, _seen=constructed, _init=real_init, **k):
            _seen.append(1)
            _init(self, *a, **k)

        with patch.object(PolymorphicValidator, "__init__", counting_init), \
                patch.object(PolymorphicValidator, "run_tests",
                             lambda self, target_test=None, _runs=runs: _runs.append(target_test) or
                             {"success": True, "output": "Tests run: 1, Failures: 0"}):
            try:
                closures = close_requirements_with_named_tests(CFG, ledger, reqs, cand, ws, modified=["pom.xml"],
                                                               revision="terminal", **kwargs)
                outcome = {"raised": None, "closures": [c.get("closed") for c in closures]}
            except Exception as exc:  # the terminal gate turns this into REQUIREMENTS_UNRESOLVED
                outcome = {"raised": f"{type(exc).__name__}: {str(exc)[:120]}"}
        report.append({"case": case, "plan_authority_passed": bool(kwargs), "validators_constructed": len(constructed),
                       "named_tests_run": runs, **outcome})
print(json.dumps(report, indent=1))
