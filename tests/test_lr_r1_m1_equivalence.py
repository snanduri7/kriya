"""LR-R1-M1 invariant I-2 (design §6.7, test T3): the recorder has zero
behavioural effect.

The same scripted run - the real pipeline over a real LLMClient, only the
runtime port scripted (tests/_chaos_harness.py) - is executed once per
recorder variant: ``full``, ``digest_only``, ``off``, a store that cannot be
opened, and a store whose every write fails. Every variant runs in the same
absolute workspace path (recreated between runs) so path text in prompts and
gate output is comparable. Across the variants these must be identical:

- provider request bytes (every ChatRequest the runtime received);
- workspace bytes (everything outside ``.git/`` and ``.kriya/``);
- the result dict;
- the run-event sequence of every trace row (attempt modes, retry decisions,
  fallback selections ...), apart from the one ``evidence.attempt_store``
  pointer event;
- the RunRecord and the decision ledger.

Only values that differ between two runs of the *same* variant are masked,
and they are measured, not guessed: ``off`` runs twice and every JSON path
whose value differs between those two identical runs (timings, run ids,
workspace ids, git commit hashes, transaction ids) is masked in every
observation. Any other difference across variants is a recorder effect.

Durations written into text are nondeterministic in themselves and are
normalized in every compared text, request bytes included: pytest's summary
(``1 failed in 0.02s``, quoted by gate output and retry prompts) and Kriya's
own messages (``... completed for 1 file(s) in 0.01s``). They vary between
two runs of the same variant (measured), independently of the recorder.
"""
import dataclasses
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pytest
from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    benign_roles,
    chaos_config,
    chaos_engine,
    git_workspace,
    run_direct,
)

from kriya.core.attempt_evidence import reader
from kriya.core.attempt_evidence import writer as writer_module
from kriya.core.state_paths import ENV_STATE_DIR, trace_db_path

GOAL = "add sub to calc.py"
VARIANTS = ("off", "off_again", "full", "digest_only", "open_fails", "write_fails")
COMPARED = ("result", "events", "run_records", "decisions")


def _volatile_paths(a: Any, b: Any, path=()) -> set:
    """JSON paths whose values differ between two runs of the same variant."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = set()
        for key in set(a) | set(b):
            if key not in a or key not in b:
                out.add(path + (key,))
            else:
                out |= _volatile_paths(a[key], b[key], path + (key,))
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = set()
        for index, (x, y) in enumerate(zip(a, b, strict=True)):
            out |= _volatile_paths(x, y, path + (index,))
        return out
    return set() if a == b else {path}


# Wall-clock timing is excluded from I-2 by design (§14 R5: outputs are
# identical, timing is not); two identical runs can round a duration alike.
_TIMING_KEY = re.compile(r"(seconds|latency|_ms|duration_sec)$")
_DURATION_TEXT = re.compile(r"\bin \d+(?:\.\d+)?s\b")


def _untimed(text: str) -> str:
    return _DURATION_TEXT.sub("in <t>s", text)


def _mask(value: Any, paths: set, path=()) -> Any:
    if path in paths:
        return "<volatile>"
    if isinstance(value, dict):
        return {k: "<timing>" if _TIMING_KEY.search(str(k)) else _mask(v, paths, path + (k,))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v, paths, path + (i,)) for i, v in enumerate(value)]
    return value


def _tree(workspace: Path) -> Dict[str, bytes]:
    out = {}
    for path in sorted(workspace.rglob("*")):
        rel = path.relative_to(workspace).as_posix()
        if path.is_file() and not rel.startswith((".git/", ".kriya/")):
            out[rel] = path.read_bytes()
    return out


def _control(workspace: Path, name: str):
    root = workspace / ".kriya" / "control" / name
    if root.is_file():
        return [json.loads(line) for line in root.read_text().splitlines() if line.strip()]
    if not root.is_dir():
        return []
    return [json.loads(p.read_text()) for p in sorted(root.glob("*.json"))]


def _apply_variant(patch, cfg, variant: str) -> None:
    capture = {"off_again": "off", "open_fails": "full", "write_fails": "full"}.get(variant, variant)
    cfg.evidence.attempt_recorder.capture = capture
    if variant == "open_fails":
        def refuse(*_args, **_kwargs):
            raise writer_module.RecorderUnavailable("injected: disk full")
        patch.setattr(writer_module.AttemptEvidenceWriter, "__init__", refuse)
    elif variant == "write_fails":
        def fail(self, *_args, **_kwargs):
            raise OSError(28, "injected: No space left on device")
        patch.setattr(writer_module.AttemptEvidenceWriter, "append", fail)


def _observe(tmp_path: Path, monkeypatch, variant: str, responder: Callable, files: Dict[str, str],
             goal: str = GOAL, configure: Optional[Callable] = None,
             drive: Optional[Callable] = None) -> Dict[str, Any]:
    """``configure(cfg, patch)`` adjusts the scenario's configuration (and may
    patch, inside the variant's patch context); ``drive(engine_factory,
    workspace)`` runs the scenario (default: one direct run of ``goal``) and
    returns the last result."""
    state = tmp_path / "states" / variant
    state.mkdir(parents=True)
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    # Identical commit hashes in every variant (a commit hash covers its
    # timestamp; two runs in different seconds would differ for no reason).
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, "2026-01-01T00:00:00+00:00")
    if (tmp_path / "ws").exists():
        shutil.rmtree(tmp_path / "ws")
    workspace = git_workspace(tmp_path, files)
    runtime = ChaosRuntime(responder)
    cfg = chaos_config()
    with monkeypatch.context() as patch:
        _apply_variant(patch, cfg, variant)
        if configure is not None:
            configure(cfg, patch)
        with RuntimeRegistration(runtime):
            if drive is None:
                result = run_direct(chaos_engine(cfg), goal, workspace)
            else:
                result = drive(lambda: chaos_engine(cfg), workspace)
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = list(db.execute("SELECT run_events FROM runs ORDER BY rowid"))
    events, pointers = [], []
    for (raw,) in rows:
        for event in json.loads(raw or "[]"):
            if event["kind"] == "evidence.attempt_store":
                pointers.append(event["details"])
            else:
                events.append(event)
    return {
        "requests": _untimed(json.dumps([dataclasses.asdict(r) for r in runtime.requests], sort_keys=True,
                                        default=str)),
        "tree": _tree(workspace),
        "result": json.loads(_untimed(json.dumps(result, default=str))),
        "events": json.loads(_untimed(json.dumps(events, default=str))),
        "run_records": json.loads(_untimed(json.dumps(_control(workspace, "runs")))),
        "decisions": json.loads(_untimed(json.dumps(_control(workspace, "decisions.jsonl")))),
        "_pointers": pointers, "_state": str(state),
    }


def _direct_success(role, request):
    return CALC_WITH_SUB if role == "developer" else benign_roles(role, request)


def _retry_responder():
    wrong = "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a + b\n"
    answers = iter([wrong] + [CALC_WITH_SUB] * 20)

    def responder(role, request):
        return next(answers) if role == "developer" else benign_roles(role, request)
    return responder


SCENARIOS = {
    # Developer answers correctly first time.
    "direct_success": lambda: _direct_success,
    # A wrong candidate fails the tests, then repair answers are refused:
    # failed attempts, retry decisions, progress vectors, a no-progress stop.
    "retry_until_stop": _retry_responder,
}


def assert_recorder_has_no_effect(tmp_path, monkeypatch, make_responder, files, label, **hooks):
    observations = {variant: _observe(tmp_path, monkeypatch, variant, make_responder(), files, **hooks)
                    for variant in VARIANTS}
    baseline, again = observations["off"], observations["off_again"]
    assert baseline["requests"] != "[]"
    for variant, observed in observations.items():
        assert observed["requests"] == baseline["requests"], (label, variant, "requests")
        assert observed["tree"] == baseline["tree"], (label, variant, "tree")
    for key in COMPARED:
        volatile = _volatile_paths(baseline[key], again[key])
        expected = _mask(baseline[key], volatile)
        for variant, observed in observations.items():
            assert _mask(observed[key], volatile) == expected, (label, variant, key)
    return observations


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_recorder_variants_never_change_the_run(tmp_path, monkeypatch, scenario):
    observations = assert_recorder_has_no_effect(
        tmp_path, monkeypatch, SCENARIOS[scenario], {"calc.py": CALC, "test_calc.py": TEST_SUB}, scenario)

    # The variants really did differ in what they recorded.
    assert {p["status"] for p in observations["full"]["_pointers"]} == {"OPEN"}
    assert {p["status"] for p in observations["off"]["_pointers"]} == {"DISABLED"}
    assert {p["status"] for p in observations["open_fails"]["_pointers"]} == {"RECORDER_UNAVAILABLE"}
    full_state = observations["full"]["_state"]
    [run_id] = reader.list_runs(full_state)
    assert reader.open_run(full_state, run_id).verify().status == reader.VERIFIED
    assert reader.list_runs(observations["off"]["_state"]) == []
    assert reader.list_runs(observations["open_fails"]["_state"]) == []
    [failed] = reader.list_runs(observations["write_fails"]["_state"])
    assert reader.open_run(observations["write_fails"]["_state"], failed).seal()["complete"] is False
    [digest_run] = reader.list_runs(observations["digest_only"]["_state"])
    blobs = Path(observations["digest_only"]["_state"]) / "attempt-evidence" / digest_run / "blobs"
    assert blobs.is_dir() and not any(p.is_file() for p in blobs.rglob("*"))


def test_capture_mode_is_part_of_no_execution_identity():
    """A capture-mode change is never config drift: neither the RunRecord's
    effective config fingerprint nor any resume fingerprint bucket sees it.
    Negative control: an ordinary setting still changes both."""
    from kriya.config.config import AppConfig
    from kriya.workflow.checkpoint import compute_config_fingerprint
    from kriya.workflow.resume_fingerprints import split_config_by_owner

    base = AppConfig()
    fingerprints, buckets = set(), []
    for capture in ("full", "digest_only", "full_with_reasoning", "off"):
        cfg = AppConfig()
        cfg.evidence.attempt_recorder.capture = capture
        cfg.evidence.attempt_recorder.retention.keep_runs = 7
        fingerprints.add(compute_config_fingerprint(cfg.model_dump()))
        buckets.append(split_config_by_owner(cfg.model_dump()))
    assert fingerprints == {compute_config_fingerprint(base.model_dump())}
    assert all(b == split_config_by_owner(base.model_dump()) for b in buckets)
    changed = AppConfig()
    changed.logging.level = "DEBUG"
    assert compute_config_fingerprint(changed.model_dump()) not in fingerprints
    assert split_config_by_owner(changed.model_dump())["config"] != split_config_by_owner(base.model_dump())["config"]
