"""REQUIREMENT TRUTH through the REAL direct pipeline (scripted model): the final result carries the backend-derived ``requirement_truth``; the launch record copies it; and the run records the same truth as an
AUTHORITATIVE ``requirement.truth`` event in its own events (persisted with the trace), so a stored run shows what the live run showed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_project_continuity_two_run import GOAL_1, Scripted, _run  # noqa: E402

from kriya.control.launch import _requirement_truth_field  # noqa: E402
from kriya.workflow.state import GenerationState  # noqa: E402


def _recorded(monkeypatch):
    events = []
    original = GenerationState.record_event

    def capture(self, event):
        events.append(event)
        return original(self, event)

    monkeypatch.setattr(GenerationState, "record_event", capture)
    return events


def test_the_final_result_carries_the_typed_truth_the_launch_record_copies_and_the_run_event_agrees(tmp_path, monkeypatch):
    events = _recorded(monkeypatch)
    result, _ = _run(tmp_path, GOAL_1, Scripted(run=1))
    assert result["quality_gates_passed"] is True, result
    truth = result["requirement_truth"]
    # the scripted verifier gives no usable verdict and this goal's behaviour has no deterministic closer: OPEN, never verified
    assert truth["status"] == "OPEN_REQUIREMENTS" and truth["verified"] == 0 and truth["total"] == 1 and truth["unresolved"]
    assert set(truth) == {"status", "verified", "accepted", "total", "unresolved"}
    assert truth["total"] == len(result["requirements"]["requirements"])
    copied = _requirement_truth_field(result)["requirement_truth"]
    assert (copied["status"], copied["total"], copied["verified"]) == (truth["status"], truth["total"], truth["verified"])
    recorded = [e for e in events if e.kind == "requirement.truth"]
    assert recorded, [e.kind for e in events]
    last = recorded[-1]
    assert last.authority.name == "AUTHORITATIVE" and {k: last.details[k] for k in truth} == truth     # the stored run replays the same truth the result carried
