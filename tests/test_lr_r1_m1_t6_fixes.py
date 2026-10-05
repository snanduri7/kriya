"""LR-R1-M1 T6 defect fixes A-F: one test group per defect, each written to
fail before its fix (design §12 T6; owner authorization 2026-10-05)."""
from _chaos_harness import CALC, TEST_SUB, benign_roles, chaos_config
from _t6_harness import direct_run

from kriya.workflow.retry_progress import NO_PROGRESS_TERMINAL_REASON

WRONG_SUB = CALC + "\n\ndef sub(a, b):\n    return a + b\n"
FILES = {"calc.py": CALC, "test_calc.py": TEST_SUB}


def _always(answer):
    return lambda role, request: answer if role == "developer" else benign_roles(role, request)


# -- A: no-progress terminal evidence ----------------------------------------------------------

def test_a_no_progress_terminal_records_its_classification_and_reason(tmp_path, monkeypatch):
    observed = direct_run(tmp_path, monkeypatch, _always(WRONG_SUB), FILES)
    progress = observed.result["retry_progress"]
    assert progress["no_progress_terminated"] is True
    decisions = observed.of("recovery.decision")
    last = decisions[-1]["payload"]
    # The runtime's own already-computed facts, not a recomputation.
    assert last["progress_classification"] == progress["classification"]
    assert last["no_progress_reason"] == NO_PROGRESS_TERMINAL_REASON == progress["terminal_reason"]
    assert last["no_progress_terminated"] is True and last["retry"] is False
    # Every earlier decision carries its own classification (the progression).
    assert all("progress_classification" in d["payload"] for d in decisions)
    assert all(d["payload"]["no_progress_reason"] is None for d in decisions[:-1])
    # explain: Q6 of the last attempt says why it stopped; Q9 names the terminal cause.
    final = observed.attempt(observed.attempts()[-1]["attempt"])
    [why] = final["Q6"]["items"]
    assert why["no_progress_reason"] == NO_PROGRESS_TERMINAL_REASON
    assert why["progress_classification"] == progress["classification"]
    q9 = observed.explained["Q9"]
    assert q9["last_recovery_decision"]["no_progress_reason"] == NO_PROGRESS_TERMINAL_REASON
    assert q9["last_recovery_decision"]["progress_classification"] == progress["classification"]


def test_a_recording_the_terminal_changes_no_retry_behaviour(tmp_path, monkeypatch):
    """The same scenario with the recorder off: identical attempt count, model
    calls and progress outcome."""
    (tmp_path / "on").mkdir()
    with_recorder = direct_run(tmp_path / "on", monkeypatch, _always(WRONG_SUB), FILES)
    cfg = chaos_config()
    cfg.evidence.attempt_recorder.capture = "off"
    (tmp_path / "off").mkdir()
    from _chaos_harness import ChaosRuntime, RuntimeRegistration, chaos_engine, git_workspace, run_direct

    from kriya.core.state_paths import ENV_STATE_DIR

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "off" / "state"))
    runtime = ChaosRuntime(_always(WRONG_SUB))
    with RuntimeRegistration(runtime):
        without = run_direct(chaos_engine(cfg), "add sub to calc.py", git_workspace(tmp_path / "off", FILES))
    # last_vector_digest covers failure text that names the (different)
    # absolute workspace path; byte identity at one path is the I-2 suite's.
    def comparable(progress):
        return {k: v for k, v in progress.items() if k != "last_vector_digest"}
    assert comparable(without["retry_progress"]) == comparable(with_recorder.result["retry_progress"])
    for key in ("failure_category", "quality_gates_passed", "candidate_gates_passed"):
        assert without.get(key) == with_recorder.result.get(key), key
    # The same model calls in the same order: the same attempts and retries.
    assert runtime.roles == with_recorder.runtime.roles
