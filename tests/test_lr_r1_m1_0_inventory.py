"""LR-R1-M1.0: executable probes behind the Attempt Evidence Recorder design.

Each probe pins a fact the recorder's identity attribution relies on
(handover/LR_R1_M1_ATTEMPT_EVIDENCE_RECORDER_DESIGN.md §5.1, §6.5, §11, R6/R7):

- a scope ContextVar set by the caller is visible inside every thread
  hand-off the evidence-emitting packages use, and inside a validator gate;
- nested mutating-run boundaries reuse the active RunContext;
- every top-level mutating run, a resume included, gets a fresh run id;
- no evidence-emitting module hands work to a thread that does not copy the
  caller's context (structural, permanent).
"""
import ast
import asyncio
import contextvars
import os
import subprocess
import threading
from pathlib import Path

from kriya.control.run_coordinator import begin_mutating_run, current_run_context
from kriya.tools.validate import PolymorphicValidator, _verification_gate

ROOT = Path(__file__).resolve().parents[1]

_PROBE: contextvars.ContextVar = contextvars.ContextVar("m1_probe", default=None)

# Evidence-emitting code (design §5): a hand-off here must carry the caller's
# context, which asyncio.to_thread does and run_in_executor/ThreadPoolExecutor/
# a bare Thread do not.
_EVIDENCE_EMITTING = (
    "kriya/workflow", "kriya/agents", "kriya/core/llm.py", "kriya/tools/validate.py",
    "kriya/core/attempt_evidence",
)
# Classified non-copying hand-offs (path -> reason). Empty at 61a867f.
_CLASSIFIED_NON_COPYING: dict = {}


def _git_workspace(path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "tracked.txt").write_text("v1\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=path,
                   check=True)
    return str(path)


def test_context_reaches_asyncio_to_thread_work():
    """Every hand-off in the evidence-emitting packages is asyncio.to_thread:
    the worker sees the caller's scope."""
    async def probe():
        _PROBE.set("scope-1")
        return await asyncio.to_thread(lambda: (_PROBE.get(), threading.get_ident()))

    seen, worker = asyncio.run(probe())
    assert seen == "scope-1"
    assert worker != threading.get_ident()


def test_validator_gate_runs_on_the_calling_thread_with_its_context(tmp_path):
    """A `_verification_gate` method runs synchronously on the caller's thread,
    so a scope set by the attempt is the scope its gate sees - directly or
    inside a to_thread hand-off (requirement-closure validators)."""
    class ProbeValidator(PolymorphicValidator):
        @_verification_gate("probe")
        def probe_gate(self):
            return {"scope": _PROBE.get(), "thread": threading.get_ident(), "gate": self._gate}

    validator = ProbeValidator(str(tmp_path))
    _PROBE.set("attempt-7")
    direct = validator.probe_gate()
    assert direct == {"scope": "attempt-7", "thread": threading.get_ident(), "gate": "probe"}

    async def handed_off():
        return await asyncio.to_thread(validator.probe_gate)

    threaded = asyncio.run(handed_off())
    assert threaded["scope"] == "attempt-7"
    assert threaded["gate"] == "probe"


def test_nested_mutating_run_reuses_the_active_context(tmp_path):
    workspace = _git_workspace(tmp_path)
    with begin_mutating_run(workspace) as outer:
        with begin_mutating_run(workspace) as inner:
            assert inner is outer
            assert current_run_context() is outer
        assert current_run_context() is outer and outer.active
    assert current_run_context() is None


def test_every_top_level_run_gets_a_fresh_run_id(tmp_path):
    """Resume included: no production caller passes run_id (structural below),
    so a resumed run is a new run whose link is RunRecord.resume_decision."""
    workspace = _git_workspace(tmp_path)
    with begin_mutating_run(workspace) as first:
        pass
    with begin_mutating_run(workspace) as second:
        pass
    assert first.run_id != second.run_id
    assert first.run_id and second.run_id


def test_no_production_caller_chooses_a_run_id():
    callers = []
    for path in (ROOT / "kriya").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) \
                    == "begin_mutating_run":
                callers.append(path.relative_to(ROOT).as_posix())
                assert not any(k.arg == "run_id" for k in node.keywords), path
    assert sorted(set(callers)) == ["kriya/cli.py", "kriya/control/run_coordinator.py"]


def _non_copying_handoffs(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", None))
            if name in ("run_in_executor", "ThreadPoolExecutor", "ProcessPoolExecutor", "Thread"):
                yield name, node.lineno


def test_evidence_emitting_code_never_drops_the_callers_context():
    """R6: a new run_in_executor/executor/Thread in an evidence-emitting module
    must be classified here (and pass the scope explicitly) before it lands."""
    found = {}
    for prefix in _EVIDENCE_EMITTING:
        target = ROOT / prefix
        files = [target] if target.is_file() else sorted(target.rglob("*.py")) if target.exists() else []
        for path in files:
            for name, line in _non_copying_handoffs(path):
                found[f"{path.relative_to(ROOT).as_posix()}:{line}"] = name
    assert set(found) == set(_CLASSIFIED_NON_COPYING), found


def test_non_copying_handoff_detector_catches_a_planted_site(tmp_path):
    """Negative control for the structural test above."""
    planted = tmp_path / "planted.py"
    planted.write_text("import asyncio\nasync def f(loop):\n    await loop.run_in_executor(None, print)\n")
    assert list(_non_copying_handoffs(planted)) == [("run_in_executor", 3)]


def test_inventory_counts_the_design_relies_on():
    """Pins the M1.0 inventory (design §5.2, notes §1/§4) so a drift is seen."""
    appends = {}
    for rel in ("kriya/workflow/attempt.py", "kriya/workflow/workflow.py", "kriya/workflow/retry_strategy.py"):
        appends[rel] = (ROOT / rel).read_text(encoding="utf-8").count("state.gate_outcomes.append(")
    # Before M1.3 these are the 85 sites; M1.3 converts them and this pin moves to the tripwire.
    assert sum(appends.values()) in (85, 0), appends
    assert os.path.exists(ROOT / "kriya/workflow/context_budget.py")
