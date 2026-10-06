"""P3-D post-fix A3 original-symptom check (harness; no model): the same frozen A3 shape as a3_reproducer_pin.py,
reporting the measurements the P3-D task asks for.

usage: PYTHONPATH=<checkout>:<checkout>/tests python a3_reproducer_postfix.py <tmpdir>"""
import hashlib
import sys
from pathlib import Path

import pytest
from _edit_protocol_harness import run_edit_protocol
from a3_reproducer_pin import GOAL, INSERT, SOURCE, TARGET
from test_prd020_milestone_requirements import _probe

from kriya.workflow.context_budget import estimate_tokens


def main(tmp):
    monkeypatch = pytest.MonkeyPatch()
    try:
        run = run_edit_protocol(Path(tmp), monkeypatch, [INSERT], probe=_probe, goal=GOAL, source=SOURCE, target=TARGET)
    finally:
        monkeypatch.undo()
    targets = [t for e in run.kinds("context.edit_capability") for t in e.details["targets"]]
    locus = targets[0]["insertion"] if targets else None
    sent = "\n".join(system + "\n" + user for system, user in run.developer)
    carrier = "\n".join(SOURCE.splitlines()[locus["start_line"] - 1:locus["end_line"]]) if locus else ""
    print("file bytes:", len(SOURCE.encode()), "| lines:", SOURCE.count("\n"),
          "| revision:", hashlib.sha256(SOURCE.encode()).hexdigest())
    print("full-file token estimate:", estimate_tokens(SOURCE))
    print("developer requests:", len(run.developer))
    print("operations:", [t["operations"] for t in targets], "| full_file authority:", [t["full_file"] for t in targets])
    print("locus:", locus)
    print("window lines:", (locus["end_line"] - locus["start_line"] + 1) if locus else None,
          "| window chars:", len(carrier))
    print("locus revision == file revision:", bool(locus) and locus["revision"] == hashlib.sha256(SOURCE.encode()).hexdigest())
    print("carrier in the fitted request sent:", bool(carrier) and carrier in sent)
    print("whole file in the request sent:", SOURCE.strip() in sent)
    print("insertion authorized events:", len(run.kinds("context.structural_insertion_authorized")))
    print("quality_gates_passed:", run.result.get("quality_gates_passed"), "| failure_category:",
          run.result.get("failure_category"))
    after = (run.workspace / TARGET).read_text()
    print("clamp applied:", "int clamp(" in after, "| pure insertion:",
          after.startswith(SOURCE.rstrip("\n").rsplit("\n", 1)[0]) and len(after) > len(SOURCE))


if __name__ == "__main__":
    main(sys.argv[1])
