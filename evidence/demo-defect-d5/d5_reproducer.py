"""D5 model-free reproducer: which candidate file does a runtime failure ground to,
and what failure evidence would the reopened owner be shown?

Replays real runtime output (the runtime-3 KNOW A s4 failure and real Spring
5.3.39 runs under `mvn -o -e exec:java`) through the production path:
  failure_grounding._build_quality_gate_failure("run_verification", ...)
  -> attribution.attribute_failure(...)            (the tier retry_strategy uses)
  -> retry_strategy's raw_evidence for the scope conflict.
No model, no network. Usage: python d5_reproducer.py <repo root>
"""
import asyncio
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT))
from kriya.workflow import retry_strategy  # noqa: E402
from kriya.workflow.attribution import attribute_failure  # noqa: E402
from kriya.workflow.failure_grounding import _build_quality_gate_failure  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
LIVE_FILES = ["pom.xml", "src/main/resources/ignite-config.xml",
              "src/main/java/com/example/IgniteDemoApplication.java"]
PROBE_FILES = ["pom.xml", "src/main/java/demo/App.java", "src/main/java/demo/Widget.java",
               "src/main/resources/bad-config.xml", "src/main/resources/widget-config.xml",
               "src/main/resources/importer.xml", "src/main/resources/imported-bad.xml"]
SYNTHETIC = """Exception in thread "main" org.springframework.beans.factory.BeanCreationException: Error creating bean \
with name 'cfg' defined in class path resource [bad-config.xml]: Error setting property values; nested exception \
is org.springframework.beans.NotWritablePropertyException: Invalid property 'noSuchProperty'
\tat org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.applyPropertyValues(\
AbstractAutowireCapableBeanFactory.java:1744)
\tat demo.App.main(App.java:7)
Caused by: org.springframework.beans.NotWritablePropertyException: Invalid property 'noSuchProperty' of bean class \
[demo.Settings]
\tat org.springframework.beans.BeanWrapperImpl.createNotWritablePropertyException(BeanWrapperImpl.java:243)
\t... 4 more
"""
CASES = [
    ("live runtime-3 s4 (app-printed trace)", (FIX / "live_runtime3_s4_output.txt").read_text(), LIVE_FILES,
     ["src/main/resources/ignite-config.xml"]),
    ("synthetic App.java loads bad-config.xml", SYNTHETIC,
     ["src/main/java/demo/App.java", "src/main/resources/bad-config.xml"], ["src/main/resources/bad-config.xml"]),
    ("real Spring: invalid property (mvn -e)", (FIX / "spring_probe_bad-config.txt").read_text(), PROBE_FILES,
     ["src/main/resources/bad-config.xml"]),
    ("real Spring: import of an invalid XML", (FIX / "spring_probe_importer.txt").read_text(), PROBE_FILES,
     ["src/main/resources/imported-bad.xml"]),
    ("real Spring: candidate bean constructor throws (control)",
     (FIX / "spring_probe_widget-config.txt").read_text(), PROBE_FILES,
     ["src/main/java/demo/App.java", "src/main/java/demo/Widget.java"]),  # unchanged: the stack locator stands
]


def raw_evidence(failure, required_files):
    """Exactly what retry_strategy records as the scope conflict's raw_evidence."""
    source = inspect.getsource(retry_strategy)
    if "grounded_evidence_excerpt(" in source:
        from kriya.workflow.failure_grounding import (
            grounded_evidence_excerpt,  # pylint: disable=import-outside-toplevel
        )
        return grounded_evidence_excerpt(failure.raw_output or "", required_files)
    return (failure.raw_output or "")[:2000]


async def main():
    report = []
    for name, output, known, expected in CASES:
        failure = _build_quality_gate_failure("run_verification", "RUNTIME VERIFICATION FAILURE", output,
                                              "/nonexistent-worktree", known, 1)
        attribution = await attribute_failure(failure, known, 0, [], None, lambda _p: None)
        evidence = raw_evidence(failure, attribution.files)
        report.append({
            "case": name, "tier": attribution.tier, "grounded_files": attribution.files,
            "expected": expected, "grounded_as_expected": attribution.files == expected,
            "raw_evidence_chars": len(evidence),
            "raw_evidence_names_grounded_file": any(Path(f).name in evidence for f in attribution.files),
            "raw_evidence_shows_exception": "Exception" in evidence,
        })
    print(json.dumps(report, indent=1))


asyncio.run(main())
