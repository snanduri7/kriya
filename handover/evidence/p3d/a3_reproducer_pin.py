"""P3-D pre-fix reproducer (harness; no model): the frozen A3 shape through the real direct workflow.
NumberUtils.java (1,696 lines, the live A3 revision c015dad6...), the frozen A3 goal, the Developer scripted at the
transport seam with a correct insertion edit it would give if asked.

usage: PYTHONPATH=<checkout>:<checkout>/tests python a3_reproducer_pin.py <tmpdir>"""
import sys
from pathlib import Path

import pytest
from _edit_protocol_harness import run_edit_protocol
from test_prd020_milestone_requirements import _probe

FIX = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "p3d"
TARGET = "src/main/java/org/apache/commons/lang3/math/NumberUtils.java"
SOURCE = (FIX / "NumberUtils.java.txt").read_text()
GOAL = (FIX / "a3_goal.txt").read_text()
CLOSING = SOURCE.rstrip("\n").rsplit("\n", 1)[1]  # the class's closing line
INSERT = ("FIX ANALYSIS: add clamp.\nSEARCH:\n" + SOURCE.rstrip("\n").rsplit("\n", 2)[1] + "\n" + CLOSING + "\nREPLACE:\n"
          + SOURCE.rstrip("\n").rsplit("\n", 2)[1] + "\n\n    public static int clamp(final int value, final int min, final int max) {\n"
          "        if (min > max) {\n            throw new IllegalArgumentException(\"min > max\");\n        }\n"
          "        return Math.max(min, Math.min(max, value));\n    }\n" + CLOSING + "\n")


def main(tmp):
    monkeypatch = pytest.MonkeyPatch()
    try:
        run = run_edit_protocol(Path(tmp), monkeypatch, [INSERT], probe=_probe, goal=GOAL, source=SOURCE, target=TARGET)
    finally:
        monkeypatch.undo()
    capabilities = [e.details for e in run.kinds("context.edit_capability")]
    failures = [e.message[:200] for e in run.kinds("failure.recorded")]
    print("developer requests:", len(run.developer))
    print("edit capabilities:", [[(t["operations"], len(t["spans"]), t["loci"]) for t in c["targets"]] for c in capabilities])
    print("failures:", failures[:2])
    print("quality_gates_passed:", run.result.get("quality_gates_passed"), "| failure_category:", run.result.get("failure_category"))
    print("clamp applied:", "int clamp(" in (run.workspace / TARGET).read_text())


if __name__ == "__main__":
    main(sys.argv[1])
