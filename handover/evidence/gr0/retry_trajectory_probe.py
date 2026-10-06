"""GR-R0 Part 1 measurement (no model): the per-attempt trajectory of the
edit-protocol harness after an anchor miss whose capability cannot change,
with and without a fallback model. Run with the tests dir on sys.path."""
import json
import sys
import tempfile
from pathlib import Path

import pytest
from _edit_protocol_harness import CORRECT_EDIT, FABRICATED_EDIT, run_edit_protocol
from test_prd020_milestone_requirements import _probe


class _MP:
    def __init__(self):
        self._mp = pytest.MonkeyPatch()

    def setattr(self, *a, **k):
        self._mp.setattr(*a, **k)

    def undo(self):
        self._mp.undo()


def trajectory(fallback):
    mp = _MP()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            run = run_edit_protocol(Path(tmp), mp, [FABRICATED_EDIT], probe=_probe,
                                    fallback=fallback, fallback_answers=[CORRECT_EDIT])
            caps = {e.attempt: (e.details["model"], e.details["targets"][0]["digest"][:10])
                    for e in run.kinds("context.edit_capability")}
            fails = {e.attempt: e.message.split(":")[0] if "ANCHOR" in e.message else e.message[:60]
                     for e in run.kinds("failure.recorded")}
            rows = [{"attempt": a, "model": caps[a][0], "digest": caps[a][1], "failure": fails.get(a, "-")}
                    for a in sorted(caps)]
            return {"fallback": fallback, "developer_requests": run.developer_models, "attempts": rows,
                    "transitions": [e.attempt for e in run.kinds("retry.strategy_transition")],
                    "status": run.result.get("status"), "failure_category": run.result.get("failure_category")}
    finally:
        mp.undo()


if __name__ == "__main__":
    out = json.dumps([trajectory("fallback-model"), trajectory(None)], indent=1)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(out)
    print(out)
    sys.exit(0)
