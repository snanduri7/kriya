"""LR-R1-M1.2: evidence scopes, run/unit/attempt lifecycle, config + SEC-009.

Design §5.1, §6.5, §7, §9.4; tests T7 (identity), T14 (SEC-009, D5 deferred:
an unopenable store is RECORDER_UNAVAILABLE and the run continues).
"""
import asyncio
import contextlib
import os
import subprocess

import pytest
import yaml

from kriya.config.authority import ConfigAuthorityError
from kriya.config.config import load_config
from kriya.control.run_coordinator import begin_mutating_run
from kriya.core.attempt_evidence import reader, scope
from kriya.core.state_paths import ENV_STATE_DIR
from tests._strict_doubles import strict_config


def _git_workspace(path):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    with open(os.path.join(path, "tracked.txt"), "w") as handle:
        handle.write("v1\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=path,
                   check=True)
    return str(path)


def _state_dir():
    return os.environ[ENV_STATE_DIR]


def _records(run_id):
    return list(reader.open_run(_state_dir(), run_id).records())


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


# -- configuration + SEC-009 (T14) ---------------------------------------------

def test_packaged_default_capture_is_full(tmp_path):
    with _cwd(tmp_path / "ws"):
        recorder = load_config().evidence.attempt_recorder
    assert recorder.capture == "full"
    assert recorder.retention.keep_runs == 200 and recorder.retention.max_bytes == 5 * 1024 ** 3


@pytest.mark.parametrize("value", [{"capture": "off"}, {"capture": "digest_only"},
                                   {"capture": "full_with_reasoning"}, {"retention": {"keep_runs": 1}}])
def test_repository_config_can_never_change_the_recorder(tmp_path, value):
    workspace = tmp_path / "ws"
    with _cwd(workspace):
        with open(workspace / "kriya.yaml", "w") as handle:
            yaml.dump({"evidence": {"attempt_recorder": value}}, handle)
        with pytest.raises(ConfigAuthorityError, match="evidence"):
            load_config()


def test_unknown_capture_mode_is_rejected():
    with pytest.raises(ValueError):
        strict_config(evidence={"attempt_recorder": {"capture": "partial"}})


# -- run lifecycle ---------------------------------------------------------------

def test_run_scope_opens_on_first_configured_scope_and_seals_at_exit(tmp_path):
    workspace = _git_workspace(tmp_path / "ws")
    cfg = strict_config()
    with begin_mutating_run(workspace) as context:
        assert scope.store_pointer()["status"] == scope.STATUS_PENDING
        scope.ensure_store(cfg)
        pointer = scope.store_pointer()
        assert pointer["status"] == scope.STATUS_OPEN and pointer["capture"] == "full"
        with scope.unit_scope(cfg, "direct", "direct", {"plan_id": None}, content={"goal": "g"}) as closing:
            closing["status"] = "success"
    run = reader.open_run(_state_dir(), context.run_id)
    assert run.verify().status == reader.VERIFIED
    kinds = [r["kind"] for r in run.records()]
    assert kinds == ["run.opened", "unit.opened", "unit.closed", "run.closed"]
    closed = [r for r in run.records() if r["kind"] == "run.closed"][0]
    # A run that commits nothing ends FAILURE in its RunRecord; run.closed
    # reports exactly what the record says.
    assert closed["payload"]["terminal_status"] == "FAILURE"
    assert closed["payload"]["lifecycle_state"] == "FAILURE"
    assert closed["payload"]["unit_invocations"] == {"direct": 1}
    unit_opened = [r for r in run.records() if r["kind"] == "unit.opened"][0]
    assert run.blob(unit_opened["blobs"]["goal"]) == b"g"
    assert scope.store_pointer() == {"status": "NO_RUN_SCOPE"}


def test_a_failing_run_is_closed_and_sealed_with_its_terminal_state(tmp_path):
    workspace = _git_workspace(tmp_path / "ws")
    with pytest.raises(RuntimeError):
        with begin_mutating_run(workspace) as context:
            scope.ensure_store(strict_config())
            raise RuntimeError("boom")
    run = reader.open_run(_state_dir(), context.run_id)
    assert run.verify().status == reader.VERIFIED
    closed = list(run.records())[-1]
    assert closed["kind"] == "run.closed" and closed["payload"]["terminal_status"] == "FAILURE"


def test_keyboard_interrupt_propagates_and_the_store_still_seals(tmp_path):
    workspace = _git_workspace(tmp_path / "ws")
    with pytest.raises(KeyboardInterrupt):
        with begin_mutating_run(workspace) as context:
            scope.ensure_store(strict_config())
            raise KeyboardInterrupt
    assert reader.open_run(_state_dir(), context.run_id).verify().status == reader.VERIFIED


def test_capture_off_opens_no_store_and_says_so(tmp_path):
    workspace = _git_workspace(tmp_path / "ws")
    with begin_mutating_run(workspace) as context:
        scope.ensure_store(strict_config(evidence={"attempt_recorder": {"capture": "off"}}))
        assert scope.store_pointer() == {"run_id": context.run_id, "status": scope.STATUS_DISABLED,
                                         "capture": "off", "reason": "capture: off"}
        scope.emit("mirror.event", {"x": 1})
    assert reader.list_runs(_state_dir()) == []


def test_a_test_double_config_opens_no_store(tmp_path):
    workspace = _git_workspace(tmp_path / "ws")
    with begin_mutating_run(workspace):
        scope.ensure_store(None)
        assert scope.store_pointer()["status"] == scope.STATUS_DISABLED
    assert reader.list_runs(_state_dir()) == []


def test_unopenable_store_is_recorder_unavailable_and_the_run_continues(tmp_path, monkeypatch, caplog):
    blocked = tmp_path / "state-is-a-file"
    blocked.write_text("not a directory")
    monkeypatch.setenv(ENV_STATE_DIR, str(blocked))
    workspace = _git_workspace(tmp_path / "ws")
    with begin_mutating_run(workspace):
        scope.ensure_store(strict_config())
        pointer = scope.store_pointer()
        with scope.unit_scope(strict_config(), "direct", "direct") as closing:
            closing["status"] = "success"
        scope.emit("mirror.event", {"x": 1})
    assert pointer["status"] == "RECORDER_UNAVAILABLE" and "RecorderUnavailable" in pointer["reason"]
    assert "RECORDER_UNAVAILABLE" in caplog.text


def test_no_attempt_evidence_unavailable_blocker_exists():
    """D5 deferred: no execution blocker of that name anywhere in kriya/."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kriya")
    for directory, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(directory, name), encoding="utf-8") as handle:
                    assert "ATTEMPT_EVIDENCE_UNAVAILABLE" not in handle.read(), name


# -- identity (T7) ---------------------------------------------------------------

def test_units_attempts_and_invocations_are_attributed_from_scope(tmp_path):
    workspace = _git_workspace(tmp_path / "ws")
    cfg = strict_config()
    counter = {"n": 0}
    with begin_mutating_run(workspace) as context:
        for _ in range(2):
            with scope.unit_scope(cfg, "s1", "structured"):
                with scope.attempt_scope(lambda: counter["n"]):
                    counter["n"] += 1
                    scope.attempt_opened({"mode": "full_set"})
                    with scope.call_scope("developer") as call_seq:
                        with scope.wire_scope():
                            scope.emit("model.request", {})
                        with scope.wire_scope():
                            scope.emit("model.request", {})
                    assert call_seq is not None
        with scope.unit_scope(cfg, "s2", "structured"):
            with scope.call_scope("planner"):
                scope.emit("model.request", {})
    records = _records(context.run_id)
    requests = [r for r in records if r["kind"] == "model.request"]
    ident = [(r["unit_id"], r["invocation_seq"], r["phase"], r["attempt_number"], r["call_seq"], r["wire_seq"],
              r["role"]) for r in requests]
    assert ident == [
        ("s1", 1, "attempt", 1, 1, 1, "developer"), ("s1", 1, "attempt", 1, 1, 2, "developer"),
        ("s1", 2, "attempt", 2, 2, 1, "developer"), ("s1", 2, "attempt", 2, 2, 2, "developer"),
        ("s2", 1, "planning", None, 3, None, "planner"),
    ]
    assert all(r["run_id"] == context.run_id for r in records)
    closes = [r for r in records if r["kind"] == "attempt.closed"]
    assert [c["attempt_number"] for c in closes] == [1, 2]


def test_attempt_closed_records_a_failure_outcome_and_reraises(tmp_path):
    workspace = _git_workspace(tmp_path / "ws")

    class QualityGateFailure(Exception):
        pass
    with begin_mutating_run(workspace) as context:
        scope.ensure_store(strict_config())
        with pytest.raises(QualityGateFailure):
            with scope.attempt_scope(lambda: 1):
                raise QualityGateFailure()
    closed = [r for r in _records(context.run_id) if r["kind"] == "attempt.closed"][0]
    assert closed["payload"] == {"outcome": "FAILED", "error_type": "QualityGateFailure"}


def test_sequential_runs_never_cross_attribute(tmp_path):
    workspace = _git_workspace(tmp_path / "ws")
    cfg = strict_config()
    with begin_mutating_run(workspace) as first:
        scope.ensure_store(cfg)
        scope.emit("mirror.event", {"run": 1})
    scope.emit("mirror.event", {"after": True})   # no scope: no-op
    with begin_mutating_run(workspace) as second:
        scope.ensure_store(cfg)
        scope.emit("mirror.event", {"run": 2})
    one = [r["payload"] for r in _records(first.run_id) if r["kind"] == "mirror.event"]
    two = [r["payload"] for r in _records(second.run_id) if r["kind"] == "mirror.event"]
    assert one == [{"run": 1}] and two == [{"run": 2}]


def test_concurrent_tasks_keep_their_own_scopes(tmp_path):
    class _Context:
        def __init__(self, run_id):
            self.run_id = run_id

    async def task(run_id, n):
        with scope.run_scope(_Context(run_id)):
            scope.ensure_store(strict_config())
            for i in range(n):
                scope.emit("mirror.event", {"i": i})
                await asyncio.sleep(0)

    async def both():
        await asyncio.gather(task("run-a", 3), task("run-b", 2))

    asyncio.run(both())
    assert [r["payload"]["i"] for r in _records("run-a") if r["kind"] == "mirror.event"] == [0, 1, 2]
    assert [r["payload"]["i"] for r in _records("run-b") if r["kind"] == "mirror.event"] == [0, 1]
    assert {r["run_id"] for r in _records("run-a")} == {"run-a"}


def test_a_scope_whose_run_differs_from_the_store_is_a_gap(tmp_path):
    workspace = _git_workspace(tmp_path / "ws")
    with begin_mutating_run(workspace) as context:
        scope.ensure_store(strict_config())
        run = scope._RUN.get()
        run.run_id = "someone-else"
        scope.emit("mirror.event", {"x": 1})
        run.run_id = context.run_id
    records = _records(context.run_id)
    assert "mirror.event" not in [r["kind"] for r in records]
    gap = [r for r in records if r["kind"] == "recorder.gap"][0]
    assert gap["payload"]["lost_kind"] == "mirror.event"
    assert reader.open_run(_state_dir(), context.run_id).seal()["complete"] is False


def test_emit_never_raises_on_a_writer_bug(tmp_path, monkeypatch):
    workspace = _git_workspace(tmp_path / "ws")
    with begin_mutating_run(workspace) as context:
        scope.ensure_store(strict_config())

        def broken(*_a, **_k):
            raise TypeError("bug")
        with monkeypatch.context() as patch:
            patch.setattr(scope._RUN.get().writer, "record", broken)
            scope.emit("mirror.event", {"x": 1})   # logged, not raised
        scope.emit("mirror.event", {"x": 2})
    assert [r["payload"] for r in _records(context.run_id) if r["kind"] == "mirror.event"] == [{"x": 2}]
