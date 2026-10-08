"""LR-R1-M1.3: mirroring of the existing evidence streams (design §5.2, T13).

Every RunEvent, Decision, EvidenceRecord and gate outcome is mirrored into
the attempt evidence store exactly once, the moment it is recorded, as the
existing object unchanged (content), with its content-free identifying
fields in the payload.
"""
import ast
import json
import os
import subprocess
from pathlib import Path

import pytest

from kriya.control.decisions import DecisionLedger
from kriya.control.run_coordinator import begin_mutating_run
from kriya.core.attempt_evidence import reader, scope
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.workflow.failure import Failure
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.run_trace import write_outcome_trace
from kriya.workflow.state import GenerationState
from tests._strict_doubles import strict_config

ROOT = Path(__file__).resolve().parents[1]


def _git_workspace(path):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    Path(path, "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "a.txt"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "b"], cwd=path, check=True)
    return str(path)


def _run(tmp_path, body, capture="full"):
    workspace = _git_workspace(tmp_path / "ws")
    with begin_mutating_run(workspace) as context:
        scope.ensure_store(strict_config(evidence={"attempt_recorder": {"capture": capture}}))
        body()
    run = reader.open_run(os.environ[ENV_STATE_DIR], context.run_id)
    return run, [r for r in run.records() if r["kind"].startswith("mirror.")]


def _content(run, record, name):
    return json.loads(run.blob(record["blobs"][name]))


def test_run_event_is_mirrored_once_with_the_object_unchanged(tmp_path):
    state = GenerationState()
    event = RunEvent(kind="retry.progress_vector", attempt=2, source="retry", authority=EventAuthority.ADVISORY,
                     message="m", details={"classification": "NO_PROGRESS"})
    run, mirrors = _run(tmp_path, lambda: state.record_event(event))
    assert state.run_events == [event]
    [mirror] = mirrors
    assert mirror["kind"] == "mirror.event" and mirror["provenance"] == "MIRRORED"
    assert mirror["payload"] == {"kind": "retry.progress_vector", "attempt": 2, "source": "retry",
                                 "authority": "advisory", "failure_type": None, "operation": None}
    assert _content(run, mirror, "event") == json.loads(json.dumps(event.to_dict()))


def test_failure_is_mirrored_as_event_and_evidence(tmp_path):
    state = GenerationState()
    failure = Failure(type="test", message="TEST FAILURE: boom", attempt=1, source="quality_gate",
                      likely_files=["calc.py"], raw_output="E assert 4 == 2")
    run, mirrors = _run(tmp_path, lambda: state.record_failure(failure))
    assert [m["kind"] for m in mirrors] == ["mirror.event", "mirror.evidence"]
    evidence = _content(run, mirrors[1], "evidence")
    assert evidence["payload"]["raw_output"] == "E assert 4 == 2"
    assert len(state.evidence_records) == 1 and len(state.run_events) == 1


def test_decision_is_mirrored(tmp_path):
    ledger = DecisionLedger()
    run, mirrors = _run(tmp_path, lambda: ledger.record("structured_plan_validation", valid=False,
                                                        reason_codes=["X"]))
    [mirror] = mirrors
    assert mirror["payload"] == {"type": "structured_plan_validation"}
    assert _content(run, mirror, "decision")["reason_codes"] == ["X"]
    assert len(ledger.all()) == 1


def test_outcome_trace_row_events_are_mirrored(tmp_path):
    event = RunEvent(kind="planning.failed", attempt=0, source="enforce", authority=EventAuthority.AUTHORITATIVE,
                     message="m", details={"reason_codes": ["A"]}).to_dict()
    db = tmp_path / "t.db"
    run, mirrors = _run(tmp_path, lambda: write_outcome_trace(str(db), run_id="r.enforce", goal="g",
                                                              status="failed", llm=None, source="enforce",
                                                              events=[event]))
    assert [m["payload"]["kind"] for m in mirrors] == ["planning.failed"]


def test_gate_outcome_is_appended_unchanged_and_mirrored_once(tmp_path):
    state = GenerationState()
    outcome = {"attempt": 1, "type": "compile", "success": True, "output": "ok"}
    run, mirrors = _run(tmp_path, lambda: state.record_gate_outcome(outcome))
    assert state.gate_outcomes == [outcome] and state.gate_outcomes[0] is outcome
    [mirror] = mirrors
    assert mirror["payload"] == {"index": 0, "type": "compile", "success": True, "attempt": 1}
    assert _content(run, mirror, "outcome") == {"index": 0, **outcome}


def test_restored_gate_outcomes_replace_the_list_and_record_once(tmp_path):
    state = GenerationState()
    state.gate_outcomes.append({"type": "old"})
    restored = [{"type": "compile", "success": True}, {"type": "test", "success": True}]
    run, mirrors = _run(tmp_path, lambda: state.restore_gate_outcomes(restored, source="checkpoint"))
    assert state.gate_outcomes == restored and state.gate_outcomes is not restored
    [mirror] = mirrors
    assert mirror["kind"] == "mirror.gate_outcomes_restored"
    assert mirror["payload"] == {"count": 2, "source": "checkpoint"}
    assert _content(run, mirror, "restored")["outcomes"] == restored


def test_digest_only_mirrors_carry_no_content(tmp_path):
    canary = "CANARY-GATE-OUTPUT-91c2"
    state = GenerationState()
    run, mirrors = _run(tmp_path, lambda: state.record_gate_outcome({"type": "test", "output": canary}),
                        capture="digest_only")
    assert mirrors[0]["blobs"] == {} and mirrors[0]["content_digests"]["outcome"]["bytes"] > len(canary)
    for path in Path(run.directory).rglob("*"):
        if path.is_file():
            assert canary.encode() not in path.read_bytes()


def test_without_a_run_scope_nothing_is_mirrored_and_state_is_unchanged():
    state = GenerationState()
    state.record_gate_outcome({"type": "compile"})
    state.record_event(RunEvent(kind="k", attempt=0, source="s", authority=EventAuthority.ADVISORY, message=""))
    assert len(state.gate_outcomes) == 1 and len(state.run_events) == 1


def test_a_mirror_failure_never_changes_the_recorded_object(tmp_path, monkeypatch):
    """Quality bar 4: the broad catches in the mirror path keep the producer's
    own output - asserted on the failure path and on the normal path."""
    state = GenerationState()

    def body():
        with monkeypatch.context() as patch:
            def explode(*_a, **_k):
                raise TypeError("bug")
            patch.setattr(scope._RUN.get().writer, "append", explode)
            state.record_gate_outcome({"type": "compile"})
            state.record_gate_outcome(["not", "a", "mapping"])   # mirror cannot build it: still appended
        state.record_gate_outcome({"type": "test"})
    run, mirrors = _run(tmp_path, body)
    assert state.gate_outcomes == [{"type": "compile"}, ["not", "a", "mapping"], {"type": "test"}]
    assert [m["payload"]["index"] for m in mirrors] == [2]


def test_no_production_code_reads_the_store():
    """Design §9.1: production imports only ``scope``; the reader is for
    consumers (CLI evidence commands are added in M1.9 and listed here)."""
    allowed = {"kriya/core/attempt_evidence/reader.py",
               # Consumers (M1.9): the evidence CLI and the explainer it is built on.
               "kriya/cli.py", "kriya/core/attempt_evidence/explain.py",
               # BACKEND-READINESS-004 consumers: the blob-level leak check and the rejected-candidate export
               # (`kriya evidence leak-check` / `evidence candidate`), read-only over a sealed store.
               "kriya/core/attempt_evidence/leak_check.py", "kriya/core/attempt_evidence/candidate_export.py",
               # The doctor's evidence.attempt_recorder row (read-only diagnostics, outside the
               # workflow/control/policy/agents/tools/core packages design §9.1 forbids).
               "kriya/production_doctor.py"}
    for path in (ROOT / "kriya").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "attempt_evidence" in node.module:
                names = {alias.name for alias in node.names}
                assert "reader" not in names and not node.module.endswith(".reader"), rel
            if isinstance(node, ast.Import):
                assert not any(alias.name.endswith("attempt_evidence.reader") for alias in node.names), rel


def test_metrics_never_import_the_recorder():
    for path in (ROOT / "kriya/metrics").rglob("*.py"):
        assert "attempt_evidence" not in path.read_text(encoding="utf-8"), path


# -- T13 tripwire: gate outcomes have one producer ------------------------------

_MUTATORS = {"append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse", "__setitem__",
             "__delitem__", "__iadd__"}


def _direct_gate_outcome_mutations(source: str):
    """(line, form) for every direct mutation or reassignment of a
    ``.gate_outcomes`` attribute."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _MUTATORS:
            owner = node.func.value
            if isinstance(owner, ast.Attribute) and owner.attr == "gate_outcomes":
                found.append((node.lineno, f".gate_outcomes.{node.func.attr}()"))
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        for target in targets:
            base = target.value if isinstance(target, ast.Subscript) else target
            if isinstance(base, ast.Attribute) and base.attr == "gate_outcomes":
                found.append((node.lineno, f"{type(node).__name__} to .gate_outcomes"))
    return found


def test_gate_outcomes_are_only_mutated_inside_generation_state():
    offenders = {}
    for path in (ROOT / "kriya").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "kriya/workflow/state.py":
            continue
        hits = _direct_gate_outcome_mutations(path.read_text(encoding="utf-8"))
        if hits:
            offenders[rel] = hits
    assert offenders == {}, ("record gate outcomes with GenerationState.record_gate_outcome (or "
                             f"restore_gate_outcomes on resume), never directly: {offenders}")


@pytest.mark.parametrize("planted", [
    "state.gate_outcomes.append(x)\n",
    "state.gate_outcomes.extend(xs)\n",
    "state.gate_outcomes.insert(0, x)\n",
    "state.gate_outcomes += [x]\n",
    "state.gate_outcomes = list(old)\n",
    "state.gate_outcomes[0] = x\n",
    "del state.gate_outcomes[0]\n",
])
def test_tripwire_catches_every_direct_form(planted):
    assert _direct_gate_outcome_mutations(planted), planted


def test_state_py_mutates_gate_outcomes_only_in_its_two_producers():
    tree = ast.parse((ROOT / "kriya/workflow/state.py").read_text(encoding="utf-8"))
    owners = set()
    for function in ast.walk(tree):
        if isinstance(function, ast.FunctionDef):
            if _direct_gate_outcome_mutations(ast.unparse(function)):
                owners.add(function.name)
    assert owners == {"record_gate_outcome", "restore_gate_outcomes"}
