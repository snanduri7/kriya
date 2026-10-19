"""Runtime verification and managed-service verification (full modularization W4, E9): characterization written and
green at 76f5e9a, BEFORE the runtime-verification code moves out of ``kriya/workflow/attempt.py`` into
``kriya/workflow/runtime_verification.py`` (OI-28, the runtime and managed-service half).

Nothing above the process boundary is scripted except the model: the approval decision, the runtime-command subject
and its fingerprint binding, the ExecutionPolicy audit, the verification-gate decorator (tree checks, ``gate.result``),
the runtime-verification contract, the artifact preparation and the managed-service lifecycle all run for real. The
process itself is faked BELOW them: ``kriya.tools.process.ProcessController.run`` answers the application's command
(and ``mvn package``) from the case and hands every other command to the real controller, and the managed service is
started by a fake ``ProcessController.start_managed`` with its readiness and probe answered at their source
(``kriya.tools.service_runtime._check_readiness`` / ``_run_probe``).

What is pinned:

- **Engine level** (the real controller, engine and attempt; only the model scripted): the ORDERED trace of every
  approval request (source review / runtime-command review), every judge and grader call, every application spawn
  (argv, stdin) and every attempt-evidence record the run emits (``gate.result`` stage/gate/success,
  ``mirror.gate_outcome`` type/success, ``candidate.change``), plus the result: a direct runtime verification success
  (autonomous, and held for the source review in human-in-the-loop), a failing runtime command then a passing one, a
  required runtime REJECT (the runtime-command review declined) stopping the run, an optional check declined, the
  argv and stdin contract, a contract the injector cannot satisfy, a managed-service verification (approved, blocked
  without an approval subject, a non-local host refused, a shell-compound contract refused) and a JVM finite command
  whose jar is prepared (``mvn package``) before it runs. Goldens ``tests/golden/runtime/engine_*.json``.
- **Attempt level** (the runtime functions called through ``kriya.workflow.attempt`` by the names the move keeps; the
  real ``PolymorphicValidator`` with the process faked as above; attempt-evidence capture ON): the approve-and-execute
  path (approval asked/declined/absent/authored/already approved/changed while asked, the contract, the post-review
  guard, artifact preparation and its three failure shapes, every grading branch, self-correction and its stdin-bound
  re-run, infrastructure and entrypoint stops, skill verification), the direct (verification-only) path, the
  managed-service path and its admission checks. Goldens ``tests/golden/runtime/attempt_*.json``.
- **Ordering pins** (capture ON): approval before execution before evidence; a refusal is recorded and nothing
  spawns; the post-review guard refuses before the artifact preparation; a blocked managed service is decided before
  the interpreter is resolved.
- **Unit pins** of the pure decisions (execution mode, shell-compound detection, the argv/stdin contract, advisory-only,
  the judgment basis, Python grounding) and of the Maven acquisition boundary of a runtime step (tooling-only, the
  run's root deadline).

Every golden was WRITTEN from 76f5e9a (``KRIYA_WRITE_RUNTIME_GOLDEN=1``) and is only ever compared afterwards; the
forms are byte-identical under several PYTHONHASHSEED values, basetemps of different lengths, ``-n 3`` and serial, and
a git-archive copy at another path.
"""
import asyncio
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import developer_double, strict_config

from kriya.core.kernel import Kernel
from kriya.policy.filesystem import WriteScopeMode
from kriya.tools.process import ProcessController, ProcessResult
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.file_integrity import raw_digest
from kriya.workflow.state import GenerationState

sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position

GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "runtime"
WRITE_ENV = "KRIYA_WRITE_RUNTIME_GOLDEN"
LONG_TEXT = 240   # a string longer than this is pinned by digest + length
PYTHON = sys.executable


def _compare(name: str, form: Dict[str, Any]) -> None:
    path = GOLDEN_DIR / f"{name}.json"
    text = json.dumps(form, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get(WRITE_ENV):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    assert path.is_file(), f"no golden {path}: written once from the pre-move commit with {WRITE_ENV}=1"
    assert json.loads(text) == json.loads(path.read_text(encoding="utf-8"))


def _short(value: Any) -> Any:
    """Long free text (process output, file content) as digest + length; everything else unchanged."""
    if isinstance(value, str) and len(value) > LONG_TEXT:
        return f"{kg.digest(value.encode('utf-8'))}+{len(value)}"
    if isinstance(value, dict):
        return {str(k): _short(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_short(v) for v in value]
    return value


def _interpreter_literals() -> Dict[str, str]:
    """The interpreter a Python runtime command runs under, as a token (it is this machine's path)."""
    return {PYTHON: "python", os.path.realpath(PYTHON): "python"}


# ============================================================================= the process boundary

class _Processes:
    """``ProcessController.run`` / ``start_managed`` below every Kriya decision: a command a case scripts (matched by
    ``match(argv)``) is answered from the case and recorded in the ordered trace; every other command (git, the test
    gate's pytest) runs for real."""

    def __init__(self, trace: List[Dict[str, Any]], scripted: Dict[str, Any], service: Optional[Callable[[], Any]] = None):
        self.trace = trace
        self.scripted = scripted          # label -> (match, [answer, ...]); the last answer repeats
        self.real_run = ProcessController.run
        self.service = service or _FakeService

    def _label(self, argv: List[str]) -> Optional[str]:
        for label, (match, _answers) in self.scripted.items():
            if match(argv):
                return label
        return None

    def run(self, controller: ProcessController, command: List[str], **kwargs: Any) -> ProcessResult:
        label = self._label(list(command))
        if label is None:
            # only git and the test gate's interpreter run for real: an unscripted application, build tool or JVM
            # would make a case depend on this machine's toolchain
            assert os.path.basename(command[0]) == "git" or (
                os.path.realpath(command[0]) == os.path.realpath(PYTHON) and command[1:2] == ["-c"]), command
            return self.real_run(controller, command, **kwargs)
        _match, answers = self.scripted[label]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        self.trace.append({"spawn": label, "argv": list(command), "stdin": kwargs.get("stdin_payload"),
                           "cwd": kwargs.get("cwd"), "timeout": kwargs.get("timeout"),
                           "env": kwargs.get("env") is not None,
                           "contained": kwargs.get("containment_profile") is not None})
        if callable(answer):
            answer = answer(list(command), kwargs.get("cwd"))
        return ProcessResult(returncode=answer.get("returncode", 0), stdout=answer.get("stdout", ""),
                             stderr=answer.get("stderr", ""), timeout=answer.get("timeout", False))

    def start_managed(self, controller: ProcessController, command: List[str], **kwargs: Any) -> Any:
        self.trace.append({"start_managed": list(command), "cwd": kwargs.get("cwd"),
                           "contained": _containment_form(kwargs.get("containment_profile"),
                                                          kwargs.get("containment_backend"))})
        service = self.service()
        if getattr(service, "writes", None):
            (Path(kwargs["cwd"]) / service.writes).write_text("runtime state\n")
        return service

    def patches(self) -> List[Any]:
        processes = self
        return [patch.object(ProcessController, "run", lambda controller, command, **kw: processes.run(controller, command, **kw)),
                patch.object(ProcessController, "start_managed",
                             lambda controller, command, **kw: processes.start_managed(controller, command, **kw))]


def _containment_form(profile: Any, backend: Any) -> Any:
    if profile is None and backend is None:
        return False
    return {"profile": getattr(profile, "name", type(profile).__name__), "backend": getattr(backend, "name", None)}


class _FakeService:
    """A started managed service: running until terminated (or exited with ``exit_code`` before it was ready), its
    captured output fixed; ``writes`` a runtime-state file into its working directory when it starts."""

    exec_target = None
    toolchain_identity = None

    def __init__(self, exit_code: Optional[int] = None, stderr: str = "", writes: Optional[str] = None):
        self.alive = exit_code is None
        self.exit_code = exit_code
        self.stderr = stderr
        self.writes = writes

    def poll(self):
        return None if self.alive else (self.exit_code if self.exit_code is not None else -15)

    def terminate(self, reap_timeout=None):
        self.alive = False
        return True

    def captured_output(self):
        return "service listening on 127.0.0.1:8765\n", self.stderr


def _app(argv: List[str]) -> bool:
    return any(os.path.basename(token) == "main.py" for token in argv)


def _ok(stdout: str = "6\n") -> Dict[str, Any]:
    return {"returncode": 0, "stdout": stdout, "stderr": ""}


def _crash(stderr: str = "Traceback (most recent call last):\nNameError: name 'mul' is not defined\n") -> Dict[str, Any]:
    return {"returncode": 1, "stdout": "", "stderr": stderr}


# ============================================================================= evidence capture (ordered)

RUNTIME_OUTCOME_TYPES = frozenset({"run_verification", "run_verification_hung", "verification_infrastructure_failure",
                                   "package_preparation_failed", "candidate_runtime_entrypoint_invalid",
                                   "managed_service_runtime_plan_gap"})
TRACE_EVIDENCE = frozenset({"attempt.opened", "candidate.change", "gate.result", "mirror.gate_outcome",
                            "attempt.concluded"})
TRACE_EVENTS = frozenset({"candidate_gates.passed", "approval.decision", "attempt.passed", "attempt.failed",
                          "terminal_regression.passed", "terminal_regression.failed", "auxiliary.failed"})


def _evidence_recorder(trace: List[Dict[str, Any]], *, forward: bool) -> Any:
    """``attempt_evidence.scope.emit`` wrapped: every record the run emits is appended to the ordered trace (the
    gate-relevant kinds, with their deciding fields), then handed to the real emit (``forward``) or dropped."""
    from kriya.core.attempt_evidence import scope

    real = scope.emit

    def emit(kind, payload, *args, **kwargs):
        payload = payload or {}
        row: Optional[Dict[str, Any]] = None
        if kind == "gate.result":
            row = {"evidence": kind, "stage": payload.get("stage"), "gate": payload.get("gate"),
                   "success": payload.get("success")}
        elif kind == "mirror.gate_outcome":
            row = {"evidence": kind, "type": payload.get("type"), "success": payload.get("success")}
            outcome = dict((kwargs.get("content") or {}).get("outcome") or {})
            if outcome.get("type") in RUNTIME_OUTCOME_TYPES or "graded_by" in outcome:
                row["outcome"] = {k: v for k, v in outcome.items() if k not in ("duration_seconds", "index")}
        elif kind == "candidate.change":
            row = {"evidence": kind, "path": payload.get("path")}
        elif kind == "mirror.event" and payload.get("kind") in TRACE_EVENTS:
            row = {"evidence": "event", "event": payload.get("kind")}
        elif kind in TRACE_EVIDENCE:
            row = {"evidence": kind}
        if row is not None:
            trace.append(row)
        if forward:
            return real(kind, payload, *args, **kwargs)
        return None
    return emit


# ============================================================================= engine level

MAIN = 'from calc import mul\n\nif __name__ == "__main__":\n    print(mul(2, 3))\n'
OPTIONAL_GOAL = kg.GOAL                                                     # no runtime obligation in the goal text
REQUIRED_GOAL = kg.GOAL + " Running the program main.py must print 6."      # goal_requires_runtime_behavior: True
JUDGE = {"should_run": True, "execution_mode": "finite_command", "run_commands": [["python3", "main.py"]],
         "managed_service": None, "command_source": "inferred", "input_channel": "none",
         "success_criteria": "The program prints 6", "reasoning": "main.py prints mul(2, 3)"}
PASSED = {"passed": True, "verdict": "PASS", "reasoning": "the output shows 6", "likely_files": []}
FAILED = {"passed": False, "verdict": "FAIL", "reasoning": "the program crashed before printing", "likely_files": ["calc.py"]}
SERVICE = {"service_command": ["python3", "main.py"], "readiness": {"kind": "tcp", "host": "127.0.0.1", "port": 8765},
           "probe": {"kind": "http", "host": "127.0.0.1", "port": 8765, "path": "/health", "expected_status": 200}}
JUDGE_SERVICE = {**JUDGE, "execution_mode": "managed_service", "run_commands": None, "managed_service": SERVICE}
JUDGE_PROMPT, GRADER_PROMPT = "You are the Kriya Run Verification Judge", "You are the Kriya Run Verification Grader"
RESULT_KEYS = ("quality_gates_passed", "candidate_gates_passed", "terminal_regression_passed", "overall_attempt_passed",
               "failure_category", "attempts")


class _Engine:
    """One legacy run of the calc scenario with runtime verification ON: the model scripted by role (Planner,
    Architect, Developer, Run Verification Judge / Grader, everything else a neutral PASS), the approver scripted per
    kind (source review: diffs; runtime-command review: no diffs), the processes below the decorators."""

    def __init__(self, tmp_path: Path, *, goal: str = OPTIONAL_GOAL, autonomous: bool = False,
                 judgments=(JUDGE,), grades=(PASSED,), developer=(kg.GOOD,), approve_source=(True,),
                 approve_runtime=(True,), scripted=None, approver: bool = True, configure=None,
                 files: Optional[Dict[str, str]] = None, target: str = "calc.py", patches=()):
        self.tmp_path = tmp_path
        self.goal = goal
        self.autonomous = autonomous
        self.judgments, self.grades, self.developer = list(judgments), list(grades), list(developer)
        self.approve_source, self.approve_runtime = list(approve_source), list(approve_runtime)
        self.trace: List[Dict[str, Any]] = []
        self.processes = _Processes(self.trace, scripted if scripted is not None else {"app": (_app, [_ok()])})
        self.approver = approver
        self.configure = configure
        self.files = files if files is not None else {"calc.py": kg.BASE, "test_calc.py": kg.TESTS, "main.py": MAIN}
        self.target = target
        self.extra_patches = list(patches)
        self.result: Any = None
        self.workspace = ""

    @staticmethod
    def _next(answers: List[Any]) -> Any:
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def run(self) -> "_Engine":
        from _protocol_responses import sentinel, wants_structured
        from _scripted_run import engine, git_workspace

        from kriya.core.attempt_evidence import scope
        from kriya.workflow.workflow_controller import WorkflowController

        workspace = self.tmp_path / "ws"
        workspace.mkdir()
        for rel in self.files:
            (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        self.workspace = git_workspace(workspace, self.files)

        def configure(cfg):
            cfg.paths.skills = str(self.tmp_path / "skills")
            cfg.paths.memory = str(self.tmp_path / "memory")
            cfg.skills.load_global = False
            cfg.skills.load_cwd = False
            cfg.knowledge.offline_mode = True
            cfg.autonomy.run_verification_enabled = True
            cfg.autonomy.self_correction_loop_enabled = False
            if self.autonomous:
                cfg.autonomy.mode = "guardrails"
            if self.configure is not None:
                self.configure(cfg)
        eng, llm, self.cfg = engine(chain=False, configure=configure)

        async def complete(system_prompt, user_prompt, *args, **kwargs):
            system_prompt = system_prompt or ""
            if system_prompt.startswith("You are the Kriya Planner Agent"):
                return "Step 1: add mul"
            if system_prompt.startswith("You are the Kriya Architect Agent"):
                return json.dumps({"files": [self.target]})
            if system_prompt.startswith(JUDGE_PROMPT):
                self.trace.append({"model": "judge"})
                return json.dumps(self._next(self.judgments))
            if system_prompt.startswith(GRADER_PROMPT):
                self.trace.append({"model": "grader"})
                return json.dumps(self._next(self.grades))
            if not system_prompt.startswith(("You are the Kriya Developer Agent", "You are the Kriya File List Planner")):
                return json.dumps({"verdict": "PASS", "requirements": []})
            self.trace.append({"model": "developer"})
            content = self._next(self.developer)
            if wants_structured(system_prompt):
                return sentinel(self.target, analysis="add mul", content=content)
            if "FILE CONTENT:" in user_prompt:
                return f"FIX ANALYSIS: add mul\nFILE CONTENT:\n{content}"
            return json.dumps([{"filepath": self.target, "content": content}])
        llm.complete = complete

        def approval_callback(diffs, reason):
            kind = "source" if diffs else "runtime"
            answer = self._next(self.approve_source if diffs else self.approve_runtime)
            self.trace.append({"approval": kind, "reason": kg.digest(str(reason).encode("utf-8")) if diffs else str(reason),
                               "answer": answer})
            return answer

        with patch.object(scope, "emit", _evidence_recorder(self.trace, forward=True)):
            patches = self.processes.patches() + [
                patch("kriya.tools.service_runtime._check_readiness", return_value=True),
                patch("kriya.tools.service_runtime._run_probe",
                      side_effect=lambda spec, timeout: (200, '{"status": "ok"}', True, "probe returned 200")),
                *[make(self) for make in self.extra_patches]]
            for item in patches:
                item.start()
            try:
                self.result = asyncio.run(WorkflowController(eng).execute(
                    migration_mode="legacy", goal=self.goal, workspace_path=self.workspace,
                    approval_callback=approval_callback if self.approver else None))
            finally:
                for item in reversed(patches):
                    item.stop()
        return self

    def legacy(self) -> Dict[str, Any]:
        return getattr(self.result, "legacy_result", self.result) or {}

    def form(self) -> Dict[str, Any]:
        from kriya.context.workspace_identity import workspace_identity
        from kriya.core.state_paths import ENV_STATE_DIR

        state_dir = os.environ[ENV_STATE_DIR]
        norm = kg.Normalizer({os.path.realpath(self.workspace): "workspace", self.workspace: "workspace",
                              workspace_identity(self.workspace): "workspace_id",
                              os.path.realpath(state_dir): "state_dir", state_dir: "state_dir",
                              os.path.realpath(str(self.tmp_path)): "tmp", str(self.tmp_path): "tmp",
                              **_interpreter_literals()})
        legacy = self.legacy()
        # normalized FIRST (paths, run ids, the interpreter become tokens), then long text digested
        return _short(norm.value({"trace": self.trace,
                                  "result": {key: legacy.get(key) for key in RESULT_KEYS if key in legacy}}))

    def spawned(self, label: str = "app") -> List[Dict[str, Any]]:
        return [row for row in self.trace if row.get("spawn") == label]



@pytest.fixture
def git_identity(monkeypatch):
    for key, value in kg.GIT_ENV.items():
        monkeypatch.setenv(key, value)


REVISED = kg.GOOD + "\n# revised\n"
NON_LOCAL_SERVICE = {**SERVICE, "readiness": {**SERVICE["readiness"], "host": "example.com"}}
SHELL_SERVICE = {**SERVICE, "service_command": ["sh", "-c", "python3 main.py && curl 127.0.0.1:8765/health"]}

# scenario -> (constructor arguments, the terminal path the golden header names)
ENGINE_SCENARIOS: Dict[str, Any] = {
    "immediate_success": (dict(autonomous=True), "autonomous: judged, executed, graded PASS, success"),
    "deferred_success": (dict(), "human-in-the-loop: the source review, then the runtime-command review, then the run"),
    "immediate_failure_then_success": (
        dict(autonomous=True, developer=(REVISED, kg.GOOD), scripted={"app": (_app, [_crash(), _ok()])},
             grades=(FAILED, PASSED)),
        "autonomous: the first run crashes (graded FAIL, a retry), the second passes"),
    "required_reject": (
        dict(goal=REQUIRED_GOAL, approve_runtime=(False,)),
        "required runtime REJECT: the source approved, the runtime-command review declined -> "
        "REQUIRED_RUNTIME_VERIFICATION_DECLINED stop, nothing executed"),
    "optional_decline": (
        dict(approve_runtime=(False,)),
        "optional runtime check declined: nothing executed, run_verification_declined, success"),
    "stdin_contract": (
        dict(autonomous=True, judgments=({**JUDGE, "input_channel": "stdin"},)),
        "input_channel stdin: the run gets the synthetic input on stdin"),
    "argv_contract": (
        dict(autonomous=True, judgments=({**JUDGE, "input_channel": "argv"},)),
        "input_channel argv: the synthetic input appended to the final command"),
    "contract_incomplete": (
        dict(autonomous=True, judgments=({**JUDGE, "input_channel": "argv", "run_commands": [["mvn", "exec:exec"]]},)),
        "input_channel argv on a build-tool command the injector cannot extend: "
        "RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE, nothing executed"),
    "managed_service_approved": (
        dict(autonomous=True, judgments=(JUDGE_SERVICE,)),
        "managed service (autonomous): started, ready, probed (PROBE_PASSED), terminated"),
    "managed_service_blocked_required": (
        dict(goal=REQUIRED_GOAL, judgments=(JUDGE_SERVICE,)),
        "managed service in human-in-the-loop, required: MANAGED_SERVICE_APPROVAL_UNSUPPORTED stop, nothing started"),
    "managed_service_blocked_optional": (
        dict(judgments=(JUDGE_SERVICE,)),
        "managed service in human-in-the-loop, optional: recorded as declined, nothing started, success"),
    "managed_service_non_local_host": (
        dict(autonomous=True, judgments=({**JUDGE_SERVICE, "managed_service": NON_LOCAL_SERVICE},)),
        "managed service whose readiness host is not local: MANAGED_SERVICE_CONTRACT_INVALID, nothing started"),
    "managed_service_shell_compound": (
        dict(autonomous=True, judgments=({**JUDGE_SERVICE, "managed_service": SHELL_SERVICE},)),
        "managed service given as one shell-compound command: MANAGED_SERVICE_CONTRACT_INVALID, nothing started"),
}


@pytest.mark.parametrize("name", sorted(ENGINE_SCENARIOS))
def test_engine_runtime_verification_trace(name, tmp_path, git_identity):
    kwargs, terminal = ENGINE_SCENARIOS[name]
    run = _Engine(tmp_path, **kwargs).run()
    _compare(f"engine_{name}", {"terminal_path": terminal, **run.form()})


# ============================================================================= attempt level

CALC = "def add(a, b):\n    return a + b\n"
CALC_NEW = CALC + "\n\ndef mul(a, b):\n    return a * b\n"
REPO = {"calc.py": CALC, "main.py": MAIN, "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"}
ACTIVE = "=== ACTIVE CODE CONTEXT ==="


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=str(repo), check=True,
                   capture_output=True)


def _trees(tmp_path: Path, files: Dict[str, str], candidate: Dict[str, str]):
    """A git workspace (the repository) and a separate worktree holding the repository plus the candidate."""
    workspace, worktree = tmp_path / "ws", tmp_path / "wt"
    for root in (workspace, worktree):
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    for rel, text in candidate.items():
        (worktree / rel).parent.mkdir(parents=True, exist_ok=True)
        (worktree / rel).write_text(text)
    return str(workspace), str(worktree)


def _tree(root: str) -> Dict[str, str]:
    """relpath -> content digest of every file under ``root`` (outside .git and bytecode caches)."""
    found: Dict[str, str] = {}
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache"))
        for name in sorted(files):
            path = Path(directory) / name
            found[str(path.relative_to(root))] = kg.digest(path.read_bytes())
    return found


def _changes(before: Dict[str, str], after: Dict[str, str]) -> Dict[str, Any]:
    return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "changed": sorted(rel for rel in set(before) & set(after) if before[rel] != after[rel])}


class _Writer:
    """Attempt-evidence capture ON without a store: the scope reports an open writer, ``emit`` is recorded."""

    capture = "full"
    capture_reasoning = False
    run_id = "attempt-run"


class _Skills:
    """The skill engine as the success bookkeeping sees it: the looked-up skill (``source_path`` when the case gives
    one; ``fail`` makes the lookup raise) and every verification mark."""

    def __init__(self, trace: List[Dict[str, Any]], source_path: Optional[str] = None, fail: bool = False):
        self.trace = trace
        self.source_path = source_path
        self.fail = fail

    def get_skill(self, name: str) -> Any:
        import types

        if self.fail:
            raise KeyError(name)
        return types.SimpleNamespace(name=name, tags=[], source_path=self.source_path)

    def mark_verified(self, name: str, *, context: str) -> None:
        self.trace.append({"skill_verified": name, "context": context})


class _Repair:
    """The runtime self-correction micro-loop's outcome (``run_self_correction_loop`` scripted at its source)."""

    def __init__(self, resolved: bool):
        self.resolved = resolved
        self.turns_used = 2
        self.final_compile_output = "recompiled: ok"
        self.transcript = [{"turn": 1, "action": "patched calc.py"}]
        self.incidents = [{"source": "self_correction", "message": "one turn refused", "type": "edit_refused"}]
        self.scope_conflict_files = [] if resolved else ["outside.py"]


def _config(**autonomy: Any):
    """Runtime verification on, a non-default run timeout (so the timeout handed to every process is visible)."""
    return strict_config(autonomy={"run_verification_enabled": True, "spec_compliance_enabled": False,
                                   "self_correction_loop_enabled": False, "mode": "guardrails",
                                   "run_verification_timeout_seconds": 37, **autonomy})


class _AttemptRun:
    """One call of a runtime-verification function through ``kriya.workflow.attempt`` (by the name the move keeps),
    the real validator over a real worktree, the process boundary and the model scripted, evidence capture ON."""

    def __init__(self, tmp_path: Path, *, files=None, candidate=None, cfg=None, judgments=(JUDGE,), grades=(PASSED,),
                 approvals=(True,), approver: bool = True, scripted=None, repair=None, ctx=None,
                 written=("calc.py",), goal: str = OPTIONAL_GOAL, required: bool = False, service=None,
                 readiness: bool = True, probe=(200, '{"status": "ok"}', True, "probe returned 200"), prepare=None,
                 patches=(), skills: Optional[Dict[str, Any]] = None):
        self.tmp_path = tmp_path
        self.trace: List[Dict[str, Any]] = []
        files = REPO if files is None else files
        candidate = {"calc.py": CALC_NEW} if candidate is None else candidate
        self.workspace, self.worktree = _trees(tmp_path, files, candidate)
        self.cfg = cfg if cfg is not None else _config()
        self.judgments, self.grades, self.approvals = list(judgments), list(grades), list(approvals)
        self.processes = _Processes(self.trace, scripted if scripted is not None else {"app": (_app, [_ok()])},
                                    service=service)
        self.readiness, self.probe = readiness, probe
        self.extra_patches = list(patches)
        self.repair = repair
        self.state = GenerationState()
        self.state.attempt_number = 1
        self.state.all_files_written = set(written)
        self.state.files_written = sorted(self.state.all_files_written)
        for rel in candidate:
            self.state.candidate_digests.setdefault(rel, raw_digest((Path(self.worktree) / rel).read_bytes()))
        self.skills = _Skills(self.trace, **(skills or {}))
        self.ctx = self._ctx(goal=goal, required=required, approver=approver, **(ctx or {}))
        self.validator = PolymorphicValidator(self.worktree, original_workspace_path=self.workspace,
                                              autonomy_cfg=self.cfg.autonomy)
        if prepare is not None:
            prepare(self)
        self.before = _tree(self.worktree)
        self.outcome: Any = "returned"
        self.failure: Any = None

    def _ctx(self, *, goal: str, required: bool, approver: bool, **overrides: Any) -> "attempt.AttemptContext":
        trace = self.trace

        async def judge(**kwargs):
            trace.append({"model": "judge", "files_written": kwargs.get("files_written"),
                          "build_file_content": kwargs.get("build_file_content") is not None})
            return dict(self.judgments.pop(0) if len(self.judgments) > 1 else self.judgments[0])

        async def grade(**kwargs):
            trace.append({"model": "grader", "timed_out": kwargs.get("timed_out", False),
                          "returncode": kwargs.get("returncode"), "files_written": kwargs.get("files_written"),
                          "distrust_notice": "distrust_notice" in kwargs, "retained_evidence": "evidence" in kwargs})
            answer = self.grades.pop(0) if len(self.grades) > 1 else self.grades[0]
            if isinstance(answer, BaseException):
                raise answer
            return dict(answer)

        def approval_callback(diffs, reason):
            answer = self.approvals.pop(0) if len(self.approvals) > 1 else self.approvals[0]
            trace.append({"approval": "source" if diffs else "runtime", "reason": str(reason), "answer": answer})
            return answer

        run_verifier = AsyncMock()
        run_verifier.judge = AsyncMock(side_effect=judge)
        run_verifier.grade = AsyncMock(side_effect=grade)
        defaults = dict(
            goal=goal, plan="Step 1: add mul", design="Design: calc.py gains mul", workspace_path=self.workspace,
            worktree_path=self.worktree, architect_files=["calc.py"], resume_state=None, run_id="runtime-run",
            skills_prompt="", learned_rag_context="", matched_files=[], related_files=[], ecosystem_invariant_block="",
            resource_lifecycle_block="", verification_contract_block="", recovery_contract_block="",
            required_files_prompt_block="", required_dependencies_prompt_block="", expected_files_upfront=["calc.py"],
            architect_basename_to_path={"calc.py": "calc.py"}, chain=[], targeted_max_retries=3,
            stream_callback=None, approval_callback=approval_callback if approver else None,
            active_skills=["calc-conventions"], active_skill_rules_snapshot={}, developer=developer_double(),
            run_verifier=run_verifier, spec_compliance=AsyncMock(), skill_engine=self.skills,
            kernel=Kernel(config=self.cfg), max_retries=4, web_lookup_query_callback=None,
            approve_web_lookup=AsyncMock(return_value=False), established_files=["main.py"],
            grounding_goal=goal, runtime_verification_required=required,
        )
        defaults.update(overrides)
        return attempt.AttemptContext(**defaults)

    def _readiness(self, spec: Any) -> bool:
        self.trace.append({"readiness": f"{spec.kind}://{spec.host}:{spec.port}{spec.path}"})
        return self.readiness

    def _probe(self, spec: Any, timeout: float) -> Any:
        self.trace.append({"probe": f"{spec.method} {spec.host}:{spec.port}{spec.path}", "timeout": timeout})
        if isinstance(self.probe, BaseException):
            raise self.probe
        return self.probe

    def _interpreter(self) -> Callable[..., Any]:
        """The validator's interpreter grounding, recorded where it runs (the managed-service admission resolves it
        before any process starts)."""
        real = PolymorphicValidator._substitute_python_interpreter
        trace = self.trace

        def substitute(validator, commands):
            trace.append({"interpreter_grounding": [list(c) for c in commands]})
            return real(validator, commands)
        return substitute

    def call(self, case: Callable[["_AttemptRun"], Any]) -> "_AttemptRun":
        from kriya.core.attempt_evidence import scope

        repair = self.repair

        async def self_correction_loop(**kwargs):
            self.trace.append({"self_correction": kwargs.get("failure_type"),
                               "writable_files": sorted(kwargs.get("writable_files") or []),
                               "files_in_scope": sorted(kwargs.get("files_in_scope") or []),
                               "max_turns": kwargs.get("max_turns"),
                               "active_code_context": kwargs.get("active_code_context")})
            return repair
        patches = self.processes.patches() + [
            patch.object(scope, "_active_writer", return_value=_Writer()),
            patch.object(scope, "emit", _evidence_recorder(self.trace, forward=False)),
            patch("kriya.tools.service_runtime._check_readiness", side_effect=self._readiness),
            patch("kriya.tools.service_runtime._run_probe", side_effect=self._probe),
            patch("kriya.workflow.self_correction.run_self_correction_loop", side_effect=self_correction_loop),
            patch.object(PolymorphicValidator, "_substitute_python_interpreter", self._interpreter()),
            *[make(self) for make in self.extra_patches],
        ]
        for item in patches:
            item.start()
        try:
            asyncio.run(case(self))
        except QualityGateFailure as error:
            self.failure = error.failure
            self.outcome = "raised"
        finally:
            for item in reversed(patches):
                item.stop()
        return self

    def form(self) -> Dict[str, Any]:
        norm = kg.Normalizer({os.path.realpath(self.worktree): "wt", self.worktree: "wt",
                              os.path.realpath(self.workspace): "ws", self.workspace: "ws",
                              os.path.realpath(str(self.tmp_path)): "tmp", str(self.tmp_path): "tmp",
                              **_interpreter_literals()})
        state = self.state
        # normalized FIRST (paths, the interpreter become tokens), then long text digested
        return _short(norm.value({
            "outcome": self.outcome_form(), "trace": self.trace,
            "gate_outcomes": [{k: v for k, v in o.items() if k != "duration_seconds"} for o in state.gate_outcomes],
            "events": [{"kind": e.kind, "source": e.source, "failure_type": e.failure_type, "operation": e.operation,
                        "message": e.message} for e in state.run_events],
            "state": {"run_verification_declined": state.run_verification_declined,
                      "approved_fingerprint": state.run_verification_approved_fingerprint is not None,
                      "cached_judgment": state.cached_run_verification_judgment,
                      "cached_basis": state.cached_run_verification_basis_hash is not None,
                      "toolchain_checked": state.toolchain_checked, "java_home_override": state.java_home_override,
                      "validator_timings": [[t["kind"], t["success"], isinstance(t["duration_seconds"], float)]
                                            for t in state.validator_timings],
                      "plan_scope_conflict": state.plan_scope_conflict,
                      "validator_java_home_override": self.validator.java_home_override,
                      "validator_tree_bound": self.validator.tree_binding is not None},
            "worktree_changes": _changes(self.before, _tree(self.worktree)),
        }))

    def outcome_form(self) -> Any:
        failure = self.failure
        if failure is None:
            return "returned"
        return {"raised": "QualityGateFailure", "type": failure.type, "message": failure.message,
                "raw_output": failure.raw_output, "likely_files": failure.likely_files,
                "diagnostics": failure.diagnostics,
                "self_correction_attempt": getattr(failure, "self_correction_attempt", None)}

    def spawned(self, label: str = "app") -> List[Dict[str, Any]]:
        return [row for row in self.trace if row.get("spawn") == label]


async def _approve_and_execute(run: _AttemptRun, judgment: Optional[Dict[str, Any]] = None) -> None:
    await attempt._approve_and_execute_runtime_verification(run.state, run.ctx, run.validator, ACTIVE,
                                                            dict(judgment or JUDGE), run.cfg.autonomy)


async def _directly(run: _AttemptRun) -> None:
    await attempt._execute_runtime_verification_directly(run.state, run.ctx, run.validator)


def _managed(judgment: Dict[str, Any]) -> Callable[[_AttemptRun], Any]:
    async def case(run: _AttemptRun) -> None:
        known = sorted(set(run.state.all_files_written) | set(run.ctx.established_files))
        await attempt._execute_managed_service_verification(run.state, run.ctx, dict(judgment), known, run.validator)
    return case


HITL = {"mode": "human-in-the-loop"}
AUTHORED_GOAL = OPTIONAL_GOAL + " Verify it by running `python3 main.py`."
STDIN_JUDGE = {**JUDGE, "input_channel": "stdin"}
MARKED_CALC = CALC_NEW + "\n# prints [VERIFICATION] FAIL when mul(2, 3) != 6\n"
POM = ("<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n  <modelVersion>4.0.0</modelVersion>\n"
       "  <groupId>org.example</groupId>\n  <artifactId>app</artifactId>\n  <version>1.0</version>\n</project>\n")
APP_JAVA = "src/main/java/org/example/App.java"
JVM_REPO = {"pom.xml": POM, APP_JAVA: ("package org.example;\n\npublic class App {\n    public static void main(String[] a) "
                                       "{ System.out.println(6); }\n}\n")}
JAR = "target/app-1.0.jar"
JAR_JUDGE = {**JUDGE, "run_commands": [["java", "-jar", JAR]]}


def _maven(argv: List[str]) -> bool:
    return bool(argv) and os.path.basename(argv[0]) in ("mvn", "mvnw")


def _java(argv: List[str]) -> bool:
    return bool(argv) and os.path.basename(argv[0]) == "java" and "-jar" in argv


def _package(*, creates: bool = True, returncode: int = 0) -> Callable[[List[str], str], Dict[str, Any]]:
    """``mvn package`` (and every other Maven call): the jar is written when the build 'succeeds'."""
    def answer(argv: List[str], cwd: str) -> Dict[str, Any]:
        if "package" in argv and creates and returncode == 0:
            (Path(cwd) / "target").mkdir(exist_ok=True)
            (Path(cwd) / JAR).write_bytes(b"PK\x03\x04")
        if "package" in argv and returncode:
            return {"returncode": returncode, "stdout": "[ERROR] COMPILATION ERROR\n[INFO] BUILD FAILURE\n", "stderr": ""}
        if any(goal in argv for goal in ("compile", "clean")):
            classes = Path(cwd) / "target" / "classes" / "org" / "example"
            classes.mkdir(parents=True, exist_ok=True)
            (classes / "App.class").write_bytes(b"\xca\xfe\xba\xbe")
        return {"returncode": 0, "stdout": "[INFO] BUILD SUCCESS\n", "stderr": ""}
    return answer


def _jvm_processes(*, creates: bool = True, returncode: int = 0, app=None) -> Dict[str, Any]:
    return {"maven": (_maven, [_package(creates=creates, returncode=returncode)]), "app": (_java, [app or _ok()])}


def _with_guard(answers: List[bool]) -> Dict[str, Any]:
    """The post-source-review guard: consulted right before execution; answers in order (the last repeats)."""
    calls = list(answers)

    def guard() -> bool:
        return calls.pop(0) if len(calls) > 1 else calls[0]
    return {"post_source_review_guard": guard}


async def _already_approved(run: _AttemptRun) -> None:
    subject = attempt._derive_runtime_command_subject(run.ctx, JUDGE, "")
    run.state.run_verification_approved_fingerprint = subject.fingerprint
    await _approve_and_execute(run)


async def _changed_while_asked(run: _AttemptRun) -> None:
    """The command set changes while the person decides (every round): it is reviewed again, at most
    MAX_REVIEW_ROUNDS times, and never executed."""
    judgment = dict(JUDGE, run_commands=[["python3", "main.py"]])
    asked = run.ctx.approval_callback

    def changing(diffs, reason):
        answer = asked(diffs, reason)
        judgment["run_commands"] = [judgment["run_commands"][0] + ["--again"]]
        return answer
    run.ctx.approval_callback = changing
    await attempt._approve_and_execute_runtime_verification(run.state, run.ctx, run.validator, ACTIVE, judgment,
                                                            run.cfg.autonomy)


def _judged(judgment: Dict[str, Any]) -> Callable[[_AttemptRun], Any]:
    async def case(run: _AttemptRun) -> None:
        await _approve_and_execute(run, judgment)
    return case


# case -> (_AttemptRun arguments, the function called, the path it pins)
ATTEMPT_CASES: Dict[str, Any] = {
    # ---- approve and execute (the candidate gates' runtime half)
    "ae_autonomous_success": (dict(), _approve_and_execute, "autonomous: no review, executed, graded PASS, skills verified"),
    "ae_hitl_approved": (dict(cfg=_config(**HITL)), _approve_and_execute,
                         "human-in-the-loop: the exact command set reviewed and approved, then executed"),
    "ae_hitl_declined_optional": (dict(cfg=_config(**HITL), approvals=(False,)), _approve_and_execute,
                                  "declined, not required: recorded as declined, nothing executed, returns"),
    "ae_hitl_declined_required": (dict(cfg=_config(**HITL), approvals=(False,), required=True), _approve_and_execute,
                                  "declined, required: REQUIRED_RUNTIME_VERIFICATION_DECLINED, nothing executed"),
    "ae_hitl_no_approver_optional": (dict(cfg=_config(**HITL), approver=False), _approve_and_execute,
                                     "no approval callback, not required: fails closed as declined, nothing executed"),
    "ae_hitl_no_approver_required": (dict(cfg=_config(**HITL), approver=False, required=True), _approve_and_execute,
                                     "no approval callback, required: REQUIRED_RUNTIME_VERIFICATION_DECLINED"),
    "ae_hitl_goal_authored": (dict(cfg=_config(**HITL), goal=AUTHORED_GOAL), _approve_and_execute,
                              "the user's own exact command (terminal callback): not asked, executed"),
    "ae_hitl_already_approved": (dict(cfg=_config(**HITL)), _already_approved,
                                 "the same command set already approved in this run: not asked again, executed"),
    "ae_hitl_changed_while_asked": (dict(cfg=_config(**HITL)), _changed_while_asked,
                                    "the set changed while asked (every round): re-reviewed MAX_REVIEW_ROUNDS times, "
                                    "never executed, declined"),
    "ae_hitl_contract_incomplete": (dict(cfg=_config(**HITL)),
                                    _judged({**JUDGE, "input_channel": "argv", "run_commands": [["mvn", "exec:exec"]]}),
                                    "an unsatisfiable contract is not offered for review: "
                                    "RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE, nothing executed"),
    "ae_stdin": (dict(), _judged(STDIN_JUDGE), "input_channel stdin: the synthetic input reaches the process"),
    "ae_argv_java_class": (dict(scripted={"app": (lambda argv: "App" in argv, [_ok()])}),
                           _judged({**JUDGE, "input_channel": "argv", "run_commands": [["java", "-cp", "out", "App"]]}),
                           "input_channel argv on a java launch: the input after the class, not a JVM option value"),
    "ae_argv_exec_java": (dict(scripted={"app": (_maven, [_ok()])}),
                          _judged({**JUDGE, "input_channel": "argv",
                                   "run_commands": [["mvn", "exec:java", "-Dexec.mainClass=App"]]}),
                          "input_channel argv on mvn exec:java: -Dexec.args=<input>"),
    "ae_post_review_guard_stale": (dict(scripted=_jvm_processes(), files=JVM_REPO, candidate={APP_JAVA: JVM_REPO[APP_JAVA]},
                                        written=(APP_JAVA,), ctx=_with_guard([False])), _judged(JAR_JUDGE),
                                   "the candidate changed after the source review: refused before the artifact "
                                   "preparation, nothing built or executed"),
    "ae_post_review_guard_stale_after_preparation": (
        dict(scripted=_jvm_processes(), files=JVM_REPO, candidate={APP_JAVA: JVM_REPO[APP_JAVA]}, written=(APP_JAVA,),
             ctx=_with_guard([True, False])), _judged(JAR_JUDGE),
        "the candidate changed during the artifact preparation: refused before the run"),
    "ae_jar_prepared": (dict(scripted=_jvm_processes(), files=JVM_REPO, candidate={APP_JAVA: JVM_REPO[APP_JAVA]},
                             written=(APP_JAVA,)), _judged(JAR_JUDGE),
                        "java -jar of a jar no gate builds: mvn package first (finite-command artifact), then the run"),
    "ae_jar_package_build_failed": (dict(scripted=_jvm_processes(returncode=1), files=JVM_REPO,
                                         candidate={APP_JAVA: JVM_REPO[APP_JAVA]}, written=(APP_JAVA,)),
                                    _judged(JAR_JUDGE),
                                    "mvn package ran and failed: a repair-eligible 'compile' failure, nothing run"),
    "ae_jar_not_materialized": (dict(scripted=_jvm_processes(creates=False), files=JVM_REPO,
                                     candidate={APP_JAVA: JVM_REPO[APP_JAVA]}, written=(APP_JAVA,)), _judged(JAR_JUDGE),
                                "mvn package succeeded but produced no jar: package_preparation_failed, nothing run"),
    "ae_jar_no_build_system": (dict(scripted=_jvm_processes(), files={APP_JAVA: JVM_REPO[APP_JAVA]},
                                    candidate={APP_JAVA: JVM_REPO[APP_JAVA]}, written=(APP_JAVA,)), _judged(JAR_JUDGE),
                               "a missing jar and no pom.xml: verification_infrastructure_failure, nothing run"),
    "ae_nonzero_graded_fail": (dict(scripted={"app": (_app, [_crash()])}, grades=(FAILED,)), _approve_and_execute,
                               "a nonzero exit graded FAIL: run_verification failure with the grader's likely files"),
    "ae_nonzero_grader_pass": (dict(scripted={"app": (_app, [_crash()])}), _approve_and_execute,
                               "a nonzero exit the grader calls PASS: the exit status outranks the grade (PRD-025)"),
    "ae_self_correction_resolved": (
        dict(cfg=_config(self_correction_loop_enabled=True, self_correction_loop_max_turns=3),
             scripted={"app": (_app, [_crash(), _ok()])}, grades=(FAILED, PASSED), repair=_Repair(True)),
        _judged(STDIN_JUDGE),
        "a nonzero exit, self-correction resolves it: the SAME commands re-run with the SAME stdin, PASS, self_corrected"),
    "ae_self_correction_unresolved": (
        dict(cfg=_config(self_correction_loop_enabled=True, self_correction_loop_max_turns=3),
             scripted={"app": (_app, [_crash()])}, grades=(FAILED,), repair=_Repair(False)),
        _approve_and_execute, "self-correction does not resolve it: failure with its attempt recorded, scope conflict"),
    "ae_self_correction_not_after_source_review": (
        dict(cfg=_config(self_correction_loop_enabled=True), scripted={"app": (_app, [_crash()])}, grades=(FAILED,),
             repair=_Repair(True), ctx=_with_guard([True])),
        _approve_and_execute, "after the source review the micro-loop (which edits the candidate) is never entered"),
    "ae_timed_out_output_correct": (dict(scripted={"app": (_app, [{"returncode": -9, "stdout": "6\n", "timeout": True}])}),
                                    _approve_and_execute, "a hang after correct output: run_verification_hung failure"),
    "ae_timed_out_output_wrong": (dict(scripted={"app": (_app, [{"returncode": -9, "stdout": "", "timeout": True}])},
                                       grades=(FAILED,)), _approve_and_execute,
                                  "a hang without the output: run_verification failure"),
    "ae_deterministic_test_pass": (dict(scripted={"pytest": (lambda argv: "pytest" in argv, [_ok("1 passed\n")])}),
                                   _judged({**JUDGE, "run_commands": [["python3", "-m", "pytest", "-q"]]}),
                                   "a test command's exit status is the verdict (process_exit, no grader), gate 'test'"),
    "ae_deterministic_test_fail": (dict(scripted={"pytest": (lambda argv: "pytest" in argv, [_crash("1 failed\n")])}),
                                   _judged({**JUDGE, "run_commands": [["python3", "-m", "pytest", "-q"]]}),
                                   "a failing test command: process_exit FAIL, no grader"),
    "ae_contract_marker_pass": (dict(candidate={"calc.py": MARKED_CALC},
                                     scripted={"app": (_app, [_ok("[VERIFICATION] PASS: mul(2, 3) == 6\n")])}),
                                _approve_and_execute, "a grounded [VERIFICATION] PASS marker is the verdict (contract)"),
    "ae_contract_marker_fail": (dict(candidate={"calc.py": MARKED_CALC},
                                     scripted={"app": (_app, [{"returncode": 1, "stdout": "[VERIFICATION] FAIL: got 5\n"}])}),
                                _approve_and_execute, "a grounded [VERIFICATION] FAIL marker is the verdict (contract)"),
    "ae_distrusted_marker": (dict(scripted={"app": (_app, [_ok("[VERIFICATION] PASS\n")])}), _approve_and_execute,
                             "an ungrounded PASS marker: graded with the distrust notice, forced non-PASS (VER-006)"),
    "ae_distrusted_marker_grader_raises": (dict(scripted={"app": (_app, [_ok("[VERIFICATION] PASS\n")])},
                                                grades=(RuntimeError("grader backend down"),)), _approve_and_execute,
                                           "the distrusted-evidence grader call fails: a failed grade, never a pass"),
    "ae_entrypoint_missing": (dict(scripted={"app": (_app, [_crash("Traceback (most recent call last):\nModuleNotFoundError: "
                                                                    "No module named 'calcx'\n")])}),
                              _approve_and_execute,
                              "the command cannot load its entrypoint: verification infrastructure, judgment dropped"),
    "ae_command_not_executable": (dict(scripted={"app": (_app, [lambda argv, cwd: (_ for _ in ()).throw(
        FileNotFoundError("no such interpreter"))])}), _approve_and_execute,
                                  "the command could not be executed at all: verification infrastructure"),
}


@pytest.mark.parametrize("name", sorted(ATTEMPT_CASES))
def test_attempt_runtime_case(name, tmp_path):
    kwargs, case, terminal = ATTEMPT_CASES[name]
    run = _AttemptRun(tmp_path, **kwargs).call(case)
    _compare(f"attempt_{name}", {"terminal_path": terminal, **run.form()})


# ---- the direct (verification-only) path: nothing written, the repository already holds the implementation
VERIFIED_REPO = {**REPO, "calc.py": CALC_NEW}
JAVA_LIBRARY = {"src/Lib.java": "public class Lib {\n    public static int six() { return 6; }\n}\n"}
JAVA_MAIN = {"src/Main.java": "public class Main {\n    public static void main(String[] a) { System.out.println(6); }\n}\n"}
CLI = 'from calc import add\n\nif __name__ == "__main__":\n    print(add(1, 2))\n'


def _direct(**kwargs: Any) -> Dict[str, Any]:
    base = dict(files=VERIFIED_REPO, candidate={}, written=(), ctx={"established_files": ["calc.py", "main.py"]})
    ctx = {**base["ctx"], **kwargs.pop("ctx", {})}
    return {**base, **kwargs, "ctx": ctx}


def _cached(*, stale: bool) -> Callable[[_AttemptRun], None]:
    def prepare(run: _AttemptRun) -> None:
        run.state.cached_run_verification_judgment = dict(JUDGE, success_criteria="cached: prints 6")
        run.state.cached_run_verification_basis_hash = ("stale" if stale
                                                        else attempt._run_verification_basis_hash(run.ctx, run.state))
    return prepare


def _declined_before(run: _AttemptRun) -> None:
    run.state.run_verification_declined = True


def _toolchain(mismatch: bool, *, checked: bool = False) -> Callable[[_AttemptRun], None]:
    def prepare(run: _AttemptRun) -> None:
        run.state.toolchain_checked = checked
        run.state.java_home_override = "/opt/jdk-11" if checked else None
        run.toolchain = {"java_found": True, "java_version": "17", "mvn_found": True,
                         "mvn_java_version": "26" if mismatch else "17", "mismatch": mismatch}
    return prepare


async def _directly_with_toolchain(run: _AttemptRun) -> None:
    def probe():
        run.trace.append({"toolchain_probe": True})
        return run.toolchain

    def home(version):
        run.trace.append({"jdk_home_for": version})
        return "/opt/jdk-17"
    with patch("kriya.tools.validate.check_java_toolchain", side_effect=probe), \
            patch("kriya.workflow.toolchain._resolve_jdk_home_for_version", side_effect=home):
        await _directly(run)


JVM_DIRECT = dict(files={"pom.xml": POM, **JVM_REPO}, ctx={"established_files": [APP_JAVA, "pom.xml"]},
                  goal="Verify App prints 6, targeting Java 17.",
                  scripted={"jvm": (lambda argv: os.path.basename(argv[0]) in ("javac", "java"), [_ok()])},
                  judgments=(JAR_JUDGE,))

DIRECT_CASES: Dict[str, Any] = {
    "d_success": (_direct(), _directly, "verification-only: judged, the tree bound, executed, graded PASS"),
    "d_disabled_required": (_direct(cfg=_config(run_verification_enabled=False), required=True), _directly,
                            "runtime verification disabled, required: REQUIRED_RUNTIME_VERIFICATION_DISABLED"),
    "d_disabled_optional": (_direct(cfg=_config(run_verification_enabled=False)), _directly,
                            "runtime verification disabled, optional: nothing judged or run"),
    "d_declined_earlier_required": (_direct(required=True, prepare=_declined_before), _directly,
                                    "a decline carried from an earlier gate call, required: DECLINED on entry"),
    "d_declined_earlier_optional": (_direct(prepare=_declined_before), _directly,
                                    "a decline carried from an earlier gate call, optional: nothing judged or run"),
    "d_cached_judgment_reused": (_direct(prepare=_cached(stale=False)), _directly,
                                 "the cached judgment of the same basis: reused, the judge not asked"),
    "d_cached_judgment_stale": (_direct(prepare=_cached(stale=True)), _directly,
                                "the cached judgment of another basis: dropped, judged again"),
    "d_judge_unavailable_required": (_direct(required=True, judgments=({"should_run": False, "run_commands": None,
                                                                         "infrastructure_error": "judge backend down",
                                                                         "success_criteria": ""},)), _directly,
                                     "the judge unavailable while runtime is required: infrastructure failure"),
    "d_judge_unavailable_optional": (_direct(judgments=({"should_run": False, "run_commands": None,
                                                         "infrastructure_error": "judge backend down",
                                                         "success_criteria": ""},)), _directly,
                                     "the judge unavailable, optional: cached as should_run False, nothing run"),
    "d_required_not_runnable": (_direct(required=True, judgments=({**JUDGE, "should_run": False, "run_commands": None,
                                                                    "reasoning": "a library"},)), _directly,
                                "required but judged not runnable: REQUIRED_RUNTIME_VERIFICATION_MISSING (reasoning)"),
    "d_advisory_only": (_direct(ctx={"structured_plan": object(), "current_subtask_id": "s2"}), _directly,
                        "a structured unit with no runtime obligation: the judge's should_run is advisory, nothing run"),
    "d_unsupported_mode": (_direct(judgments=({**JUDGE, "execution_mode": "service"},)), _directly,
                           "an unsupported execution_mode: MANAGED_SERVICE_CONTRACT_INVALID"),
    "d_managed_service": (_direct(judgments=(JUDGE_SERVICE,)), _directly,
                          "managed_service routed to the managed-service verification: PROBE_PASSED"),
    "d_build_only": (_direct(judgments=({**JUDGE, "run_commands": [["mvn", "compile"]]},)), _directly,
                     "a build-only sequence: BEHAVIORAL_GOAL_WITH_BUILD_ONLY_VERIFICATION (any unit)"),
    "d_java_no_entrypoint": (_direct(files={**VERIFIED_REPO, **JAVA_LIBRARY},
                                     ctx={"established_files": ["src/Lib.java"]},
                                     judgments=({**JUDGE, "run_commands": [["java", "Ghost"]]},)), _directly,
                             "java, no build file, no main(): should_run forced False, nothing run"),
    "d_java_grounded": (_direct(files={**VERIFIED_REPO, **JAVA_MAIN}, ctx={"established_files": ["src/Main.java"]},
                                judgments=({**JUDGE, "run_commands": [["java", "Main"]]},),
                                scripted={"jvm": (lambda argv: os.path.basename(argv[0]) in ("javac", "java"),
                                                  [_ok()])}), _directly,
                        "java, no build file, one main(): the grounded javac + java sequence runs"),
    "d_python_target_substituted": (_direct(judgments=({**JUDGE, "run_commands": [["python3", "test_calc.py"]]},)),
                                    _directly, "a test-shaped Python target: the one real entrypoint substituted"),
    "d_python_target_ambiguous": (_direct(files={**VERIFIED_REPO, "cli.py": CLI},
                                          judgments=({**JUDGE, "run_commands": [["python3", "test_calc.py"]]},)),
                                  _directly, "an invalid Python target, two entrypoints: should_run False, nothing run"),
    "d_hitl_approved": (_direct(cfg=_config(**HITL)), _directly, "human-in-the-loop: reviewed, approved, executed"),
    "d_hitl_declined_required": (_direct(cfg=_config(**HITL), approvals=(False,), required=True), _directly,
                                 "human-in-the-loop declined, required: REQUIRED_RUNTIME_VERIFICATION_DECLINED"),
    "d_hitl_declined_optional": (_direct(cfg=_config(**HITL), approvals=(False,)), _directly,
                                 "human-in-the-loop declined, optional: declined, nothing run"),
    "d_hitl_no_approver": (_direct(cfg=_config(**HITL), approver=False), _directly,
                           "human-in-the-loop without an approver: fails closed as declined"),
    "d_contract_incomplete": (_direct(judgments=({**JUDGE, "input_channel": "argv",
                                                  "run_commands": [["mvn", "exec:exec"]]},)), _directly,
                              "an unsatisfiable argv contract: RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE, nothing run"),
    "d_stdin": (_direct(judgments=(STDIN_JUDGE,)), _directly, "input_channel stdin: the input reaches the process"),
    "d_nonzero_graded_fail": (_direct(scripted={"app": (_app, [_crash()])}, grades=(FAILED,)), _directly,
                              "a nonzero exit graded FAIL: run_verification failure (no self-correction here)"),
    "d_timed_out_output_correct": (_direct(scripted={"app": (_app, [{"returncode": -9, "stdout": "6\n",
                                                                       "timeout": True}])}), _directly,
                                   "a hang after correct output: run_verification_hung"),
    "d_timed_out_output_wrong": (_direct(scripted={"app": (_app, [{"returncode": -9, "timeout": True}])},
                                         grades=(FAILED,)), _directly, "a hang without the output"),
    "d_deterministic_test_pass": (_direct(scripted={"pytest": (lambda argv: "pytest" in argv, [_ok("1 passed\n")])},
                                          judgments=({**JUDGE, "run_commands": [["python3", "-m", "pytest"]]},)),
                                  _directly, "a test command: process_exit PASS, gate type 'test'"),
    "d_deterministic_test_fail": (_direct(scripted={"pytest": (lambda argv: "pytest" in argv, [_crash("1 failed\n")])},
                                          judgments=({**JUDGE, "run_commands": [["python3", "-m", "pytest"]]},)),
                                  _directly, "a failing test command: process_exit FAIL, gate type 'test'"),
    "d_entrypoint_missing": (_direct(scripted={"app": (_app, [_crash("ModuleNotFoundError: No module named 'calcx'\n")])}),
                             _directly, "the command cannot load its entrypoint: verification infrastructure"),
    "d_toolchain_java_mismatch": (dict(JVM_DIRECT, candidate={}, written=(), prepare=_toolchain(True)),
                                  _directly_with_toolchain,
                                  "a java unit: the toolchain preflight once (mismatch warned, JAVA_HOME resolved "
                                  "from the goal), the validator runs under it"),
    "d_toolchain_already_checked": (dict(JVM_DIRECT, candidate={}, written=(), prepare=_toolchain(False, checked=True)),
                                    _directly_with_toolchain,
                                    "the preflight already ran this run: not probed again, the run's override applied"),
}


@pytest.mark.parametrize("name", sorted(DIRECT_CASES))
def test_direct_runtime_case(name, tmp_path):
    kwargs, case, terminal = DIRECT_CASES[name]
    run = _AttemptRun(tmp_path, **kwargs).call(case)
    _compare(f"attempt_{name}", {"terminal_path": terminal, **run.form()})


# ---- the managed-service path
def _service(**changes: Any) -> Dict[str, Any]:
    return {**JUDGE_SERVICE, "managed_service": {**SERVICE, **changes}}


def _readiness(**changes: Any) -> Dict[str, Any]:
    return {"readiness": {**SERVICE["readiness"], **changes}}


def _probe(**changes: Any) -> Dict[str, Any]:
    return {"probe": {**SERVICE["probe"], **changes}}


def _environment_unavailable(run: _AttemptRun) -> Any:
    """The project interpreter cannot be prepared (the validator's typed environment result, set where it is
    resolved)."""
    def resolve(validator):
        run.trace.append({"interpreter_resolution": "PYTHON_ENVIRONMENT_UNAVAILABLE"})
        validator.python_environment_error = ("PYTHON_ENVIRONMENT_UNAVAILABLE", "venv creation failed: no ensurepip")
        return None, None
    return patch.object(PolymorphicValidator, "_resolve_python_interpreter", resolve)


def _environment_lost_after_repair(run: _AttemptRun) -> Any:
    """The project interpreter resolves for the first run and cannot be prepared any more for the re-run (the repair
    changed what the run requires)."""
    real = PolymorphicValidator._resolve_python_interpreter
    calls = []

    def resolve(validator):
        calls.append(1)
        if len(calls) == 1:
            return real(validator)
        run.trace.append({"interpreter_resolution": "PYTHON_ENVIRONMENT_UNAVAILABLE"})
        validator.python_environment_error = ("PYTHON_ENVIRONMENT_UNAVAILABLE", "venv creation failed: no ensurepip")
        return None, None
    return patch.object(PolymorphicValidator, "_resolve_python_interpreter", resolve)


def _crash_removing_the_jar(argv: List[str], cwd: str) -> Dict[str, Any]:
    """The first run fails and its jar is gone afterwards (the repair rebuilt the sources): the re-run needs it again."""
    (Path(cwd) / JAR).unlink()
    return _crash("Exception in thread \"main\" java.lang.IllegalStateException: not ready\n")


def _start_fails() -> Any:
    raise OSError("exec format error")


MANAGED_CASES: Dict[str, Any] = {
    "m_probe_passed": (dict(), _managed(JUDGE_SERVICE), "started, ready, probed 200: PROBE_PASSED, deterministic PASS"),
    "m_probe_failed": (dict(probe=(500, "boom", False, "expected 200, got 500")), _managed(JUDGE_SERVICE),
                       "the probe answers 500: a repair-eligible run_verification failure"),
    "m_probe_connection_refused": (dict(probe=ConnectionRefusedError("refused")), _managed(JUDGE_SERVICE),
                                   "the probe's connection refused: PROBE_FAILED (behavioral)"),
    "m_probe_internal_error": (dict(probe=KeyError("not a network error")), _managed(JUDGE_SERVICE),
                               "an internal error in the probe: VERIFICATION_INTERNAL_ERROR, verification infrastructure"),
    "m_hitl_blocked_required": (dict(cfg=_config(**HITL), required=True), _managed(JUDGE_SERVICE),
                                "human-in-the-loop, required: MANAGED_SERVICE_APPROVAL_UNSUPPORTED before admission"),
    "m_hitl_blocked_optional": (dict(cfg=_config(**HITL)), _managed(JUDGE_SERVICE),
                                "human-in-the-loop, optional: declined before admission, nothing started"),
    "m_not_an_object": (dict(), _managed({**JUDGE_SERVICE, "managed_service": None}),
                        "no managed_service object: MANAGED_SERVICE_CONTRACT_INVALID"),
    "m_shell_and": (dict(), _managed(_service(service_command=["python3", "main.py", "&&", "curl", "x"])),
                    "&& in the service command: shell-compound, refused"),
    "m_shell_nohup": (dict(), _managed(_service(service_command=["nohup", "python3", "main.py"])),
                      "nohup leading the service command: refused"),
    "m_shell_dash_c": (dict(), _managed(_service(service_command=["bash", "-c", "python3 main.py"])),
                       "a shell -c wrapper: refused"),
    "m_launcher_script": (dict(), _managed(_service(service_command=["bash", "start_server.sh"])),
                          "a launcher script as an ordinary argument is NOT shell orchestration: admitted"),
    "m_test_target": (dict(), _managed(_service(service_command=["python3", "test_calc.py"])),
                      "a test file as the service entrypoint: refused"),
    "m_readiness_non_local": (dict(), _managed(_service(**_readiness(host="example.com"))),
                              "a non-local readiness host: refused, nothing started"),
    "m_probe_non_local": (dict(), _managed(_service(**_probe(host="10.0.0.5.example.org"))),
                          "a non-local probe host: refused, nothing started"),
    "m_readiness_private_address": (dict(), _managed(_service(**_readiness(host="192.168.1.20"),
                                                             **_probe(host="192.168.1.20"))),
                                    "a private-network address is local by the egress boundary's definition"),
    "m_readiness_kind": (dict(), _managed(_service(**_readiness(kind="udp"))), "readiness kind udp: refused"),
    "m_readiness_port": (dict(), _managed(_service(**_readiness(port=70000))), "readiness port out of range: refused"),
    "m_readiness_bool_port": (dict(), _managed(_service(**_readiness(port=True))), "readiness port a bool: refused"),
    "m_readiness_path": (dict(), _managed(_service(**_readiness(kind="http", path="health"))),
                         "an http readiness path without '/': refused"),
    "m_probe_kind": (dict(), _managed(_service(**_probe(kind="grpc"))), "probe kind grpc: refused"),
    "m_probe_status": (dict(), _managed(_service(**_probe(expected_status=99))), "probe expected_status 99: refused"),
    "m_probe_method": (dict(), _managed(_service(**_probe(method=""))), "an empty probe method: refused"),
    "m_probe_body": (dict(), _managed(_service(**_probe(expected_body_contains=7))),
                     "a non-string expected_body_contains: refused"),
    "m_timeout": (dict(), _managed(_service(startup_timeout_seconds=0)), "a zero startup timeout: refused"),
    "m_timeouts_given": (dict(), _managed(_service(startup_timeout_seconds=3, probe_timeout_seconds=2.5,
                                                   shutdown_timeout_seconds=1)),
                         "explicit timeouts reach the spec (the probe's timeout)"),
    "m_environment_unavailable": (dict(patches=(_environment_unavailable,)), _managed(JUDGE_SERVICE),
                                  "the required interpreter environment cannot be prepared: the environment stop"),
    "m_start_failed": (dict(service=_start_fails), _managed(JUDGE_SERVICE),
                       "the service cannot start: SERVICE_START_FAILED, verification infrastructure"),
    "m_exited_before_ready": (dict(service=lambda: _FakeService(exit_code=1, stderr="Address already in use\n")),
                              _managed(JUDGE_SERVICE),
                              "the service exits before it is ready: infrastructure (output classified first)"),
    "m_runtime_plan_gap": (dict(service=lambda: _FakeService(
        exit_code=1, stderr="Traceback (most recent call last):\nModuleNotFoundError: No module named 'calc.settings'\n")),
        _managed(JUDGE_SERVICE), "a project-local module the service imports is missing: MANAGED_SERVICE_RUNTIME_PLAN_GAP"),
    "m_readiness_timeout": (dict(readiness=False), _managed(_service(startup_timeout_seconds=0.05)),
                            "never ready within its startup timeout: READINESS_TIMEOUT, verification infrastructure"),
}


@pytest.mark.parametrize("name", sorted(MANAGED_CASES))
def test_managed_service_case(name, tmp_path):
    kwargs, case, terminal = MANAGED_CASES[name]
    run = _AttemptRun(tmp_path, **kwargs).call(case)
    _compare(f"attempt_{name}", {"terminal_path": terminal, **run.form()})


# ---- a JVM finite command whose jar no gate builds (engine level)
APP_BASE = "package org.example;\n\npublic class App {\n    public static int add(int a, int b) { return a + b; }\n}\n"
APP_GOOD = APP_BASE.replace("}\n}\n", "}\n    public static int mul(int a, int b) { return a * b; }\n    "
                                      "public static void main(String[] args) { System.out.println(mul(2, 3)); }\n}\n")
APP_TEST = "src/test/java/org/example/AppTest.java"
APP_TEST_SOURCE = ("package org.example;\n\nimport org.junit.jupiter.api.Test;\n\nclass AppTest {\n"
                   "    @Test\n    void adds() { org.junit.jupiter.api.Assertions.assertEquals(3, App.add(1, 2)); }\n}\n")


def _maven_gates(run: "_Engine") -> Any:
    """``PolymorphicValidator._run_maven_cmd`` (the mvn process of the compile/test gates and the runtime
    prerequisite, the documented patch point) answered in order, recorded in the trace; the toolchain probe pinned."""
    def maven(validator, goals, cwd, *args, **kwargs):
        run.trace.append({"maven": list(goals), "tooling_only": kwargs.get("tooling_only", False)})
        if goals and goals[0] in ("clean", "compile"):
            classes = Path(cwd) / "target" / "classes" / "org" / "example"
            classes.mkdir(parents=True, exist_ok=True)
            (classes / "App.class").write_bytes(b"\xca\xfe\xba\xbe")
        if "test" in goals:
            reports = Path(cwd) / "target" / "surefire-reports"
            reports.mkdir(parents=True, exist_ok=True)
            (reports / "TEST-org.example.AppTest.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?><testsuite name="org.example.AppTest" tests="1" errors="0" '
                'skipped="0" failures="0"><testcase name="adds" classname="org.example.AppTest" time="0"/></testsuite>')
            return {"returncode": 0, "stdout": "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n"
                                               "[INFO] BUILD SUCCESS\n", "stderr": "", "timed_out": False}
        return {"returncode": 0, "stdout": "[INFO] BUILD SUCCESS\n", "stderr": "", "timed_out": False}
    return patch.object(PolymorphicValidator, "_run_maven_cmd", maven)


def _no_toolchain_mismatch(_run: "_Engine") -> Any:
    return patch("kriya.tools.validate.check_java_toolchain", return_value={
        "java_found": True, "java_version": "17", "mvn_found": True, "mvn_java_version": "17", "mismatch": False})


def test_engine_jvm_finite_command_artifact_preparation(tmp_path, git_identity):
    """A Maven project whose runtime command is ``java -jar target/app-1.0.jar``: no gate ever runs ``package``, so
    Kriya prepares the jar (``mvn package -DskipTests`` with the validator's environment, BELOW the decorator) before
    the reviewed command runs - after the compile and test gates, before the run, its evidence after the run."""
    run = _Engine(tmp_path, autonomous=True, goal="Add a static method mul(a, b) returning a * b to App.java.",
                  files={"pom.xml": POM, ".gitignore": "target/\n", APP_JAVA: APP_BASE, APP_TEST: APP_TEST_SOURCE},
                  target=APP_JAVA, developer=(APP_GOOD,), judgments=(JAR_JUDGE,),
                  scripted={"maven": (_maven, [_package()]), "app": (_java, [_ok()])},
                  patches=(_maven_gates, _no_toolchain_mismatch)).run()
    assert run.legacy()["quality_gates_passed"] is True
    order = [row.get("spawn") or ("maven:" + row["maven"][0] if "maven" in row else None) for row in run.trace]
    order = [step for step in order if step]
    assert order.index("maven") < order.index("app")          # the jar is packaged before the command runs
    _compare("engine_jvm_finite_command_artifact", {
        "terminal_path": "java -jar of a jar no gate builds: mvn package (validator environment), then the run",
        **run.form()})


# ---- the runtime step's Maven boundary (D2B) and the D4 entrypoint owner, through the approve-and-execute path
EXEC_POM = POM.replace("</project>", "  <build><plugins><plugin><groupId>org.codehaus.mojo</groupId>"
                                     "<artifactId>exec-maven-plugin</artifactId><version>3.1.0</version>"
                                     "<configuration><mainClass>org.example.Ghost</mainClass></configuration>"
                                     "</plugin></plugins></build>\n</project>")


def _maven_runtime(answer: Dict[str, Any]) -> Callable[[_AttemptRun], Any]:
    """``PolymorphicValidator._run_maven_cmd`` (below the gate decorator): every Maven invocation recorded with how it
    was asked (tooling-only acquisition, the run's root deadline, stdin); the runtime step answered from the case,
    the prerequisite build a success that leaves the classes."""
    def make(run: _AttemptRun) -> Any:
        def maven(validator, goals, cwd, *args, **kwargs):
            run.trace.append({"maven": list(goals), "tooling_only": kwargs.get("tooling_only", False),
                              "deadline_is_the_runs": kwargs.get("deadline") == validator._run_deadline(),
                              "stdin": kwargs.get("stdin_payload")})
            if goals and goals[0] in ("clean", "compile"):
                classes = Path(cwd) / "target" / "classes" / "org" / "example"
                classes.mkdir(parents=True, exist_ok=True)
                (classes / "App.class").write_bytes(b"\xca\xfe\xba\xbe")
                return {"returncode": 0, "stdout": "[INFO] BUILD SUCCESS\n", "stderr": "", "timeout": False}
            return {"stderr": "", "timeout": False, **answer}
        return patch.object(PolymorphicValidator, "_run_maven_cmd", maven)
    return make


EXEC_JAVA_JUDGE = {**JUDGE, "run_commands": [["mvn", "exec:java", "-Dexec.mainClass=org.example.App"]]}
MAVEN_CASES: Dict[str, Any] = {
    "ae_maven_runtime_step": (
        dict(files={"pom.xml": POM, **JVM_REPO}, candidate={APP_JAVA: JVM_REPO[APP_JAVA]}, written=(APP_JAVA,),
             patches=(_maven_runtime({"returncode": 0, "stdout": "6\n"}),)), _judged({**EXEC_JAVA_JUDGE, "input_channel": "stdin"}),
        "a Maven runtime step: the prerequisite build, then the step as a tooling-only acquisition within the run's "
        "root deadline, carrying the reviewed stdin"),
    "ae_maven_acquisition_incomplete": (
        dict(files={"pom.xml": POM, **JVM_REPO}, candidate={APP_JAVA: JVM_REPO[APP_JAVA]}, written=(APP_JAVA,),
             patches=(_maven_runtime({"returncode": 1, "stdout": "MAVEN_ACQUISITION_INCOMPLETE: exec-maven-plugin\n"}),)),
        _judged(EXEC_JAVA_JUDGE),
        "a plugin the runtime step needs could not be acquired: RUNTIME_VERIFICATION_DEPENDENCY_UNAVAILABLE, "
        "no grading, the judgment dropped"),
    "ae_candidate_entrypoint_invalid": (
        dict(files={"pom.xml": EXEC_POM, **JVM_REPO}, candidate={"pom.xml": EXEC_POM}, written=("pom.xml",),
             patches=(_maven_runtime({"returncode": 1, "stdout": "[ERROR] java.lang.ClassNotFoundException: "
                                                                 "org.example.Ghost\n"}),)),
        _judged({**JUDGE, "run_commands": [["mvn", "exec:java"]]}),
        "the candidate's pom selects a main class no source declares (D4 case A): "
        "candidate_runtime_entrypoint_invalid grounded to pom.xml"),
}


@pytest.mark.parametrize("name", sorted(MAVEN_CASES))
def test_maven_runtime_case(name, tmp_path):
    kwargs, case, terminal = MAVEN_CASES[name]
    run = _AttemptRun(tmp_path, **kwargs).call(case)
    _compare(f"attempt_{name}", {"terminal_path": terminal, **run.form()})


# ============================================================================= unit pins of the pure decisions

def test_the_execution_mode_is_one_of_two_or_an_infrastructure_defect():
    cases = [({}, ("finite_command", None)), ({"execution_mode": None}, ("finite_command", None)),
             ({"execution_mode": "finite_command"}, ("finite_command", None)),
             ({"execution_mode": "managed_service"}, ("managed_service", None)),
             ({"execution_mode": "service"}, ("service", "unsupported execution_mode 'service'")),
             ({"execution_mode": ["managed_service"]}, (["managed_service"],
                                                        "unsupported execution_mode ['managed_service']"))]
    assert [attempt._resolve_execution_mode(judgment) for judgment, _ in cases] == [want for _, want in cases]


def test_a_shell_compound_service_command_is_recognized_and_a_launcher_script_is_not():
    compound = [["nohup", "x"], ["/usr/bin/sleep", "1"], ["bash", "-c", "x"], ["SH", "-c", "x"], ["zsh", "-c", "x"],
                ["dash", "-c", "x"], ["ksh", "-c", "x"], ["python3", "a.py", "&&", "curl", "u"], ["a", "||", "b"],
                ["a", ";", "b"], ["a", "&"]]
    plain = [["bash", "start.sh"], ["python3", "a.py"], [], "python3 a.py && curl", ["a", 1], ["a&&b"],
             ["fish", "-c", "x"]]
    assert [attempt._looks_like_shell_compound_command(c) for c in compound] == [True] * len(compound)
    assert [attempt._looks_like_shell_compound_command(c) for c in plain] == [False] * len(plain)


def test_the_runtime_contract_reaches_the_real_invocation():
    SYN = attempt._RUNTIME_VERIFICATION_SYNTHETIC_INPUT
    apply = attempt._apply_runtime_verification_contract
    assert SYN == "kriya-verification-input"
    assert apply([], "argv") == ([], None, None)
    assert apply([["python3", "a.py"]], "none") == ([["python3", "a.py"]], None, None)
    assert apply([["python3", "a.py"]], "bogus") == ([["python3", "a.py"]], None, None)
    assert apply([["python3", "a.py"]], "stdin") == ([["python3", "a.py"]], SYN, None)
    assert apply([["setup"], ["python3", "a.py"]], "argv") == ([["setup"], ["python3", "a.py", SYN]], None, None)
    assert apply([["python3", "a.py", "--n", "1"]], "argv") == ([["python3", "a.py", "--n", "1"]], None, None)
    assert apply([["mvn", "test"]], "argv") == ([["mvn", "test"]], None, None)              # build/test: inapplicable
    assert apply([["java", "-cp", "out", "App"]], "argv") == ([["java", "-cp", "out", "App", SYN]], None, None)
    assert apply([["java", "--module-path", "m", "-Dx=1", "App"]], "argv") == (
        [["java", "--module-path", "m", "-Dx=1", "App", SYN]], None, None)
    assert apply([["java", "-cp", "out", "App", "given"]], "argv") == ([["java", "-cp", "out", "App", "given"]], None, None)
    assert apply([["/usr/bin/JAVA", "App"]], "argv") == ([["/usr/bin/JAVA", "App", SYN]], None, None)
    assert apply([["mvn", "exec:java"]], "argv") == ([["mvn", "exec:java", f"-Dexec.args={SYN}"]], None, None)
    assert apply([["mvn", "-Dexec.mainClass=A", "q"]], "argv") == (
        [["mvn", "-Dexec.mainClass=A", "q", f"-Dexec.args={SYN}"]], None, None)
    assert apply([["mvn", "exec:java", "-Dexec.args=given"]], "argv") == (
        [["mvn", "exec:java", "-Dexec.args=given"]], None, None)
    for build_tool in (["mvn", "exec:exec"], ["gradle", "run"], ["./gradlew", "run"], ["gradlew", "run"]):
        corrected, stdin, incomplete = apply([build_tool], "argv")
        assert corrected == [build_tool] and stdin is None and "build-tool invocation" in incomplete
    corrected, stdin, incomplete = apply([["app"]], "argv")
    assert corrected == [["app"]] and stdin is None and "isn't one this deterministic injector recognizes" in incomplete


def test_advisory_only_suppresses_only_an_undeclared_inference_of_a_structured_unit():
    def ctx(plan, subtask, required):
        return type("Ctx", (), {"structured_plan": plan, "current_subtask_id": subtask,
                                "runtime_verification_required": required})()
    plan = object()
    assert attempt._runtime_verification_is_advisory_only(ctx(plan, "s1", False), {"should_run": True}) is True
    assert attempt._runtime_verification_is_advisory_only(ctx(plan, "s1", True), {"should_run": True}) is False
    assert attempt._runtime_verification_is_advisory_only(ctx(plan, "s1", False), {"should_run": False}) is False
    assert attempt._runtime_verification_is_advisory_only(ctx(plan, None, False), {"should_run": True}) is False
    assert attempt._runtime_verification_is_advisory_only(ctx(plan, "", False), {"should_run": True}) is False
    assert attempt._runtime_verification_is_advisory_only(ctx(None, "s1", False), {"should_run": True}) is False


def test_the_missing_runtime_message_carries_the_judges_reasoning():
    with_reason = attempt._required_runtime_verification_missing_message({"reasoning": "a pure library"})
    without = attempt._required_runtime_verification_missing_message({"reasoning": ""})
    assert with_reason.startswith("REQUIRED_RUNTIME_VERIFICATION_MISSING: ") and with_reason.endswith(
        "Judge's own reasoning: a pure library")
    assert without.endswith("Judge's own reasoning: (no reasoning field returned by the judge)")


def test_the_judgment_basis_is_the_invocation_affecting_content(tmp_path):
    (tmp_path / "a.py").write_bytes(b"print(1)\r\n\r\n")
    (tmp_path / "b.py").write_bytes(b"B = 1\n")
    ctx = type("Ctx", (), {"goal": "g", "design": "d", "worktree_path": str(tmp_path), "established_files": ["b.py"]})()
    state = GenerationState()
    state.all_files_written = {"a.py"}
    basis = attempt._run_verification_basis_hash(ctx, state)
    (tmp_path / "a.py").write_bytes(b"print(1)\n")               # line endings / trailing whitespace only
    assert attempt._run_verification_basis_hash(ctx, state) == basis
    (tmp_path / "b.py").write_bytes(b"B = 2\n")                  # an established file's content
    changed = attempt._run_verification_basis_hash(ctx, state)
    assert changed != basis
    ctx.goal = "g2"
    assert attempt._run_verification_basis_hash(ctx, state) != changed
    ctx.goal = "g"
    ctx.design = "d2"
    assert attempt._run_verification_basis_hash(ctx, state) != changed
    ctx.design = "d"
    state.all_files_written = {"a.py", "gone.py"}                  # a missing file counts as missing, not skipped
    assert attempt._run_verification_basis_hash(ctx, state) != changed


def test_python_runtime_grounding_reads_the_real_tree_and_never_outside_it(tmp_path):
    root = tmp_path / "repo"
    files = {"pkg/__init__.py": "", "pkg/app.py": 'if __name__ == "__main__":\n    print(1)\n',
             "lib.py": "X = 1\n", "tool.py": "print('hi')\n", "test_tool.py": "print('t')\n",
             "tests/test_x.py": "def test_x():\n    pass\n", ".venv/run.py": "print(1)\n",
             "node_modules/m.py": "print(1)\n", ".hidden/h.py": "print(1)\n", "build/b.py": "print(1)\n",
             "README.md": "x\n"}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n")
    os.symlink(outside, root / "linked.py")
    all_py, packages, entrypoints = attempt._build_python_runtime_grounding(str(root))
    assert sorted(all_py) == ["lib.py", "pkg/__init__.py", "pkg/app.py", "test_tool.py", "tests/test_x.py", "tool.py"]
    assert sorted(packages) == ["pkg"]
    assert entrypoints == ["pkg/app.py", "tool.py"]
    (root / "__init__.py").write_text("")                      # the root itself a package: recorded as ""
    (root / "pkg" / "sub").mkdir()
    (root / "pkg" / "sub" / "__init__.py").write_text("")
    assert sorted(attempt._collect_python_runtime_grounding_facts(str(root))[1]) == ["", "pkg", "pkg/sub"]


def test_the_deterministic_result_provenance_field():
    field = attempt._deterministic_result_provenance_field
    assert [field(a) for a in ("process_exit", "llm_over_distrusted_evidence", "llm", "contract", "x")] == [
        "PASS", "DISTRUSTED", None, None, None]


# ============================================================================= ordering pins (capture ON)

def _steps(trace: List[Dict[str, Any]]) -> List[str]:
    """The trace as step names: approval:<kind>, model:<role>, spawn:<label>, start_managed, admission,
    gate.result:<gate>, outcome:<type>, event:<kind>, other evidence kinds."""
    out = []
    for row in trace:
        if "approval" in row:
            out.append(f"approval:{row['approval']}")
        elif "model" in row:
            out.append(f"model:{row['model']}")
        elif "spawn" in row:
            out.append(f"spawn:{row['spawn']}")
        elif "start_managed" in row:
            out.append("start_managed")
        elif "interpreter_grounding" in row:
            out.append("interpreter")
        elif row.get("evidence") == "gate.result":
            out.append(f"gate.result:{row['gate']}")
        elif row.get("evidence") == "mirror.gate_outcome":
            out.append(f"outcome:{row['type']}")
        elif row.get("evidence") == "event":
            out.append(f"event:{row['event']}")
        elif "evidence" in row:
            out.append(row["evidence"])
    return out


def test_engine_the_source_review_then_the_runtime_review_then_execution_then_its_evidence(tmp_path, git_identity):
    """Human-in-the-loop: nothing is built or run before the source approval; the runtime-command review is asked after
    the judgment and BEFORE the process; the process runs before its gate.result; the grade comes after the run and the
    recorded outcome after the grade; the candidate gates pass only after it."""
    steps = _steps(_Engine(tmp_path).run().trace)
    order = [steps.index(step) for step in ("approval:source", "gate.result:compile", "model:judge", "approval:runtime",
                                            "spawn:app", "gate.result:runtime_verification", "model:grader",
                                            "outcome:run_verification", "event:candidate_gates.passed")]
    assert order == sorted(order), steps


def test_engine_a_declined_required_review_records_the_refusal_and_spawns_nothing(tmp_path, git_identity):
    run = _Engine(tmp_path, goal=REQUIRED_GOAL, approve_runtime=(False,)).run()
    steps = _steps(run.trace)
    assert not run.spawned() and "gate.result:runtime_verification" not in steps and "model:grader" not in steps
    assert steps.index("approval:runtime") < steps.index("outcome:verification_infrastructure_failure")
    assert run.legacy()["quality_gates_passed"] is False
    refusal = [row["outcome"] for row in run.trace if row.get("type") == "verification_infrastructure_failure"]
    assert refusal and str(refusal[-1]["output"]).startswith("REQUIRED_RUNTIME_VERIFICATION_DECLINED")


@pytest.mark.parametrize("name,refusal", [
    ("ae_hitl_declined_required", "REQUIRED_RUNTIME_VERIFICATION_DECLINED"),
    ("ae_hitl_no_approver_required", "REQUIRED_RUNTIME_VERIFICATION_DECLINED"),
    ("ae_hitl_contract_incomplete", "RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE"),
    ("ae_post_review_guard_stale", "CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW"),
    ("ae_post_review_guard_stale_after_preparation", "CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW"),
    ("ae_jar_package_build_failed", "PACKAGE_PREPARATION_FAILED"),
    ("ae_jar_not_materialized", "PACKAGE_PREPARATION_FAILED"),
    ("ae_jar_no_build_system", "VERIFICATION_INFRASTRUCTURE_FAILURE"),
])
def test_a_refusal_is_recorded_and_the_reviewed_command_never_runs(name, refusal, tmp_path):
    """Capture ON: the refusal is the ONLY outcome recorded, after any review asked and any preparation run, and the
    reviewed command is never spawned (no runtime gate.result, no grade)."""
    kwargs, case, _terminal = ATTEMPT_CASES[name]
    run = _AttemptRun(tmp_path, **kwargs).call(case)
    steps = _steps(run.trace)
    assert not run.spawned("app") and "gate.result:runtime_verification" not in steps and "model:grader" not in steps
    outcomes = [step for step in steps if step.startswith("outcome:")]
    assert len(outcomes) == 1 and steps[-1] == outcomes[0], steps
    assert run.failure is not None and run.failure.message.startswith(refusal), run.outcome
    if name == "ae_post_review_guard_stale":
        assert not run.spawned("maven")                       # refused before Maven is asked to prepare anything


def test_a_blocked_managed_service_is_decided_before_admission_and_starts_nothing(tmp_path):
    for required in (True, False):
        run = _AttemptRun(tmp_path / str(required), cfg=_config(**HITL), required=required).call(_managed(JUDGE_SERVICE))
        steps = _steps(run.trace)
        assert "interpreter" not in steps and "start_managed" not in steps, steps
        assert run.state.run_verification_declined is (not required)


@pytest.mark.parametrize("name", ["m_readiness_non_local", "m_probe_non_local", "m_shell_and", "m_test_target",
                                  "m_not_an_object"])
def test_an_inadmissible_managed_service_starts_nothing(name, tmp_path):
    kwargs, case, _terminal = MANAGED_CASES[name]
    run = _AttemptRun(tmp_path, **kwargs).call(case)
    steps = _steps(run.trace)
    assert "start_managed" not in steps and not any("readiness" in row or "probe" in row for row in run.trace)
    assert steps[-1] == "outcome:verification_infrastructure_failure"
    assert run.failure.message.startswith("MANAGED_SERVICE_CONTRACT_INVALID: "), run.failure.message


# ============================================================================= the patched names reach the runtime code

def test_the_runtime_seams_are_resolved_through_the_attempt_module(tmp_path):
    """Dynamic proof for the names the move must keep resolving through ``kriya.workflow.attempt`` at call time: a patch
    on each is what BOTH runtime call sites (approve-and-execute, direct) call."""
    seen: List[str] = []

    def spy(name: str, real: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(real):
            async def wrapped_async(*args, **kwargs):
                seen.append(name)
                return await real(*args, **kwargs)
            return wrapped_async

        def wrapped(*args, **kwargs):
            seen.append(name)
            return real(*args, **kwargs)
        return wrapped

    class _Clock:
        def __init__(self):
            self.now = 100.0

        def monotonic(self):
            self.now += 7.0
            return self.now

    async def both_paths(run: _AttemptRun) -> None:
        await _approve_and_execute(run)
        run.state.run_verification_approved_fingerprint = None      # the direct path asks for its own review
        await _directly(run)
        run.cfg.autonomy.mode = "guardrails"                         # a managed service has no review subject
        run.state.cached_run_verification_judgment = None              # judged again: a managed service this time
        await _directly(run)

    run = _AttemptRun(tmp_path, cfg=_config(**HITL), judgments=(JUDGE, JUDGE_SERVICE))
    with patch.object(attempt, "_approve_runtime_commands", spy("approve", attempt._approve_runtime_commands)), \
            patch.object(attempt, "_resolve_run_command", spy("resolve", attempt._resolve_run_command)), \
            patch.object(attempt, "_prepare_finite_command_runtime_artifacts",
                         spy("prepare", attempt._prepare_finite_command_runtime_artifacts)), \
            patch.object(attempt, "_check_java_toolchain_mismatch", spy("toolchain", attempt._check_java_toolchain_mismatch)), \
            patch.object(attempt, "_resolve_java_home_override", spy("java_home", attempt._resolve_java_home_override)), \
            patch.object(attempt, "_validate_and_convert_managed_service_contract",
                         spy("admission", attempt._validate_and_convert_managed_service_contract)), \
            patch.object(attempt, "_execute_managed_service_verification",
                         spy("managed", attempt._execute_managed_service_verification)), \
            patch.object(attempt, "time", _Clock()):
        run.call(both_paths)
    # approve-and-execute and direct: the review, the subject's and the executor's resolution, the preparation; the
    # direct path's toolchain preflight (python: no JAVA_HOME); the managed-service admission
    assert seen.count("approve") == 2 and seen.count("prepare") == 2 and seen.count("toolchain") == 1, seen
    assert seen.count("resolve") == 8 and "java_home" not in seen and seen.count("admission") == 1, seen
    assert seen.count("managed") == 1, seen       # the direct path hands a managed service over by the attempt name
    timings = run.state.validator_timings         # the direct path's run and the managed service, timed on attempt's clock
    assert [(t["kind"], t["duration_seconds"]) for t in timings] == [("run_verification", 7.0),
                                                                     ("managed_service_verification", 7.0)]


# ============================================================================= pins the pre-move mutants asked for

def _typed_callback(run: _AttemptRun) -> Dict[str, Any]:
    """A desktop launch's typed callback: the runtime-command subject itself is reviewed, approved by fingerprint."""
    from kriya.policy.approval import RuntimeCommandDecision, TypedApprovalCallback

    def review(subject):
        run.trace.append({"typed_review": {"unit_id": subject.unit_id, "criterion": subject.criterion,
                                           "working_directory": subject.working_directory,
                                           "commands": [list(c) for c in subject.commands],
                                           "input_channel": subject.input_channel,
                                           "stdin_bound": subject.stdin_sha256 is not None,
                                           "reason": subject.reason}})
        return RuntimeCommandDecision(subject.fingerprint)
    return {"approval_callback": TypedApprovalCallback(lambda diffs, reason: True, review),
            "current_subtask_id": "s3"}


def _writes_state(name: str) -> Callable[[List[str], str], Dict[str, Any]]:
    def answer(argv: List[str], cwd: str) -> Dict[str, Any]:
        (Path(cwd) / name).write_text("runtime state of this run\n")
        return _ok()
    return answer


def _rules_verified(run: _AttemptRun) -> Any:
    return patch("kriya.skills.skill.mark_rules_verified",
                 side_effect=lambda path, rules: run.trace.append({"rules_verified": path, "rules": list(rules)}))


class _Containment:
    """A containment profile/backend pair the validator hands out (contained execution as configured)."""

    def __init__(self, name: str):
        self.name = name


def _contained(run: _AttemptRun) -> Any:
    return patch.object(PolymorphicValidator, "build_containment_profile_and_backend",
                        lambda validator, **kwargs: (_Containment("candidate-profile"), _Containment("oci")))


CORRECTABLE_POM = "<project><properties><exec.mainClass>com.example.App</exec.mainClass></properties></project>\n"
PIN_CASES: Dict[str, Any] = {
    "ae_self_correction_deny_all": (
        dict(cfg=_config(self_correction_loop_enabled=True), scripted={"app": (_app, [_crash()])}, grades=(FAILED,),
             repair=_Repair(False), ctx={"write_scope_mode": WriteScopeMode.DENY_ALL}), _approve_and_execute,
        "a DENY_ALL unit's self-correction may write nothing"),
    "ae_self_correction_allowlist": (
        dict(cfg=_config(self_correction_loop_enabled=True), scripted={"app": (_app, [_crash()])}, grades=(FAILED,),
             repair=_Repair(False), ctx={"write_scope_mode": WriteScopeMode.ALLOWLIST,
                                         "allowed_write_relpaths": ["calc.py", "lib/extra.py"]}),
        _approve_and_execute, "an ALLOWLIST unit's self-correction may write exactly its allowed files"),
    "ae_rules_verified": (dict(skills={"source_path": "/skills/calc-conventions"},
                               ctx={"active_skill_rules_snapshot": {"calc-conventions": ["Name helpers with a verb."]}},
                               patches=(_rules_verified,)), _approve_and_execute,
                          "a passing run marks the contributing skill verified and flips exactly its snapshot rules"),
    "ae_skill_lookup_fails": (dict(skills={"fail": True}), _approve_and_execute,
                              "a skill that cannot be looked up is skipped (logged), the pass stands"),
    "ae_typed_callback_authored": (dict(cfg=_config(**HITL), goal=AUTHORED_GOAL, ctx={}), None,
                                   "a desktop (typed) callback always reviews, even the user's own exact command"),
    "ae_runtime_state_cleaned": (dict(scripted={"app": (_app, [_writes_state("tasks.json")])}), _approve_and_execute,
                                 "what the run wrote into the reused worktree is removed after it"),
    "d_goal_explicit_ungrounded": (_direct(judgments=({**JUDGE, "command_source": "goal_explicit",
                                                       "run_commands": [["python3", "test_calc.py"]]},)), _directly,
                                   "a 'goal_explicit' label the goal does not ground is downgraded: the target is "
                                   "grounded like any inferred one"),
    "d_pom_corrected": (_direct(files={"pom.xml": CORRECTABLE_POM, **JVM_REPO}, candidate={"pom.xml": CORRECTABLE_POM},
                                written=("pom.xml",), ctx={"established_files": [APP_JAVA]},
                                judgments=({**JUDGE, "run_commands": [["mvn", "exec:java"]]},),
                                scripted={"jvm": (lambda argv: os.path.basename(argv[0]) in ("javac", "java", "mvn"),
                                                  [_ok()])}), _directly,
                        "the candidate's pom corrections are applied (and the tree bound) before the run"),
    "d_runtime_state_cleaned": (_direct(scripted={"app": (_app, [_writes_state("tasks.json")])}), _directly,
                                "verification-only: what the run wrote is removed after it"),
    "ae_hitl_authored_in_the_users_request": (
        dict(cfg=_config(**HITL), goal="Unit 2 of 3: add mul.", ctx={"exit_authority_goal": AUTHORED_GOAL}),
        _approve_and_execute, "a unit goal that does not name the command, the user's request that does: not asked"),
    "ae_hitl_authored_with_stdin": (dict(cfg=_config(**HITL), goal=AUTHORED_GOAL), _judged(STDIN_JUDGE),
                                    "the user's own command with a Kriya-supplied stdin: not the user's words, asked"),
    "ae_self_correction_rerun_still_failing": (
        dict(cfg=_config(self_correction_loop_enabled=True),
             scripted={"pytest": (lambda argv: "pytest" in argv, [_crash("1 failed\n")])}, repair=_Repair(True)),
        _judged({**JUDGE, "run_commands": [["python3", "-m", "pytest"]]}),
        "a deterministic command still failing after a resolved self-correction: process_exit FAIL after repair"),
    "ae_self_correction_rerun_passing": (
        dict(cfg=_config(self_correction_loop_enabled=True),
             scripted={"pytest": (lambda argv: "pytest" in argv, [_crash("1 failed\n"), _ok("1 passed\n")])},
             repair=_Repair(True)),
        _judged({**JUDGE, "run_commands": [["python3", "-m", "pytest"]]}),
        "a deterministic command passing after a resolved self-correction: process_exit PASS, self_corrected"),
    "ae_environment_unavailable": (dict(patches=(_environment_unavailable,)), _approve_and_execute,
                                   "the project interpreter cannot be prepared: the typed environment stop, nothing run "
                                   "or graded"),
    "ae_deterministic_test_fail_naming_a_module": (
        dict(scripted={"pytest": (lambda argv: "pytest" in argv,
                                  [_crash("E   ModuleNotFoundError: No module named 'calcx'\n1 failed\n")])}),
        _judged({**JUDGE, "run_commands": [["python3", "-m", "pytest"]]}),
        "a failing test command whose output names a missing module: the process status is the verdict, never "
        "re-read as verification infrastructure"),
    "ae_command_entrypoint_invalid": (
        dict(files={"pom.xml": POM, **JVM_REPO}, candidate={APP_JAVA: JVM_REPO[APP_JAVA]}, written=(APP_JAVA,),
             patches=(_maven_runtime({"returncode": 1, "stdout": "[ERROR] java.lang.ClassNotFoundException: "
                                                                 "org.example.Ghost\n"}),)),
        _judged({**JUDGE, "run_commands": [["mvn", "exec:java", "-Dexec.mainClass=org.example.Ghost"]]}),
        "Kriya's own command names a class no source declares (D4 case B): verification infrastructure naming the "
        "entrypoint owner"),
    "ae_self_correction_rerun_environment_lost": (
        dict(cfg=_config(self_correction_loop_enabled=True), scripted={"app": (_app, [_crash()])}, grades=(FAILED,),
             repair=_Repair(True), patches=(_environment_lost_after_repair,)), _approve_and_execute,
        "the re-run after a resolved self-correction cannot prepare its environment: the typed environment stop"),
    "ae_self_correction_rerun_prepares_again": (
        dict(cfg=_config(self_correction_loop_enabled=True), files=JVM_REPO, candidate={APP_JAVA: JVM_REPO[APP_JAVA]},
             written=(APP_JAVA,), repair=_Repair(True), grades=(FAILED, PASSED),
             scripted={"maven": (_maven, [_package()]), "app": (_java, [_crash_removing_the_jar, _ok()])}),
        _judged(JAR_JUDGE), "the re-run prepares its artifact again (the jar is gone after the repair)"),
    "m_environment_verdict_wins": (
        dict(service=lambda: _FakeService(exit_code=1, stderr=(
            "Error occurred during initialization of VM\nModuleNotFoundError: No module named 'calc.settings'\n"))),
        _managed(JUDGE_SERVICE),
        "an environment verdict on the service's output outranks the missing-module plan gap: infrastructure"),
    "m_contained": (dict(patches=(_contained,)), _managed(JUDGE_SERVICE),
                    "the validator's containment profile and backend reach the launched service"),
    "m_runtime_state_cleaned": (dict(service=lambda: _FakeService(writes="service.db")), _managed(JUDGE_SERVICE),
                                "what the service wrote into the reused worktree is removed after it"),
}


@pytest.mark.parametrize("name", sorted(PIN_CASES))
def test_pinned_runtime_case(name, tmp_path):
    kwargs, case, terminal = PIN_CASES[name]
    run = _AttemptRun(tmp_path, **kwargs)
    if name == "ae_typed_callback_authored":
        run.ctx.approval_callback, run.ctx.current_subtask_id = _typed_callback(run).values()
        case = _approve_and_execute
    run.call(case)
    _compare(f"attempt_{name}", {"terminal_path": terminal, **run.form()})


def test_the_runtime_code_logs_through_the_attempt_modules_logger(tmp_path, caplog):
    """The runtime-verification code logs through kriya.workflow.attempt's logger (the move hands it attempt's logger,
    never its own): the contract line of a run, a refused contract, a blocked managed service."""
    import logging

    with caplog.at_level(logging.INFO, logger="kriya.workflow.attempt"):
        _AttemptRun(tmp_path / "run", judgments=(STDIN_JUDGE,)).call(_judged(STDIN_JUDGE))
        _AttemptRun(tmp_path / "incomplete").call(_judged({**JUDGE, "input_channel": "argv",
                                                            "run_commands": [["mvn", "exec:exec"]]}))
        _AttemptRun(tmp_path / "managed", cfg=_config(**HITL)).call(_managed(JUDGE_SERVICE))
    ours = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "kriya.workflow.attempt"]
    assert ("INFO", "RUNTIME_VERIFICATION_CONTRACT input_channel=stdin argument_count=2 stdin_present=True") in ours
    assert any(level == "WARNING" and text.startswith("RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE: ") for level, text in ours)
    assert any(level == "WARNING" and text.startswith("MANAGED_SERVICE_APPROVAL_UNSUPPORTED: ") for level, text in ours)
    assert any(level == "INFO" and text.startswith("Quality Gates: Runtime verification PASSED: ") for level, text in ours)
