"""D5b model-free reproducer: the recovery evidence a reopened owner is shown (retry_strategy's raw_evidence =
failure_grounding.grounded_evidence_excerpt) for the runtime-5 KNOW A stack-frame failure, and for the runtime-3 D5
resource failure (must stay correct). Usage: python d5b_reproducer.py <repo root>"""
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT))
from kriya.workflow.failure_grounding import grounded_evidence_excerpt  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "d5_runtime_resource"
CASES = [
    ("runtime-5 stack frame", "live_runtime5_s4.txt", "src/main/java/com/example/IgniteDemoApp.java",
     ["IgniteIllegalStateException", "Ignite instance with provided name doesn't exist", "IgniteDemoApp.java:19"]),
    ("runtime-3 resource", "live_runtime3_s4.txt", "src/main/resources/ignite-config.xml",
     ["NotWritablePropertyException", "ignite-config.xml", "Invalid property 'gridStartTime'"]),
]
report = []
for name, fixture, grounded, required in CASES:
    excerpt = grounded_evidence_excerpt((FIX / fixture).read_text(), [grounded])
    report.append({"case": name, "excerpt_chars": len(excerpt),
                   "contains": {text: text in excerpt for text in required},
                   "excerpt_first_line": excerpt.split("\n", 1)[0][:160]})
print(json.dumps(report, indent=1))
