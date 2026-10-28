"""The candidate-gate orchestration (full modularization W4, E8): characterization written and green at 74f01ac,
BEFORE the gate code moves out of ``kriya/workflow/attempt.py`` into ``kriya/workflow/gate_orchestrator.py`` (OI-28,
the JVM-toolchain half).

What is pinned, at boundaries the move does not rebind:

- **Engine level** (the real controller, engine and attempt; only the model scripted): for the representative
  terminal paths - success, a failing test gate then success, a failing compile gate then success, static analysis
  BLOCKED, the held (deferred, human-in-the-loop) executing gates and the immediate (autonomous) ones, a JVM project
  whose toolchain preflight finds a java/mvn mismatch, the verification-only unit of a structured run - the ORDERED
  attempt-evidence sequence: ``attempt.opened`` / ``candidate.change`` / every validator ``gate.result`` (stage, gate,
  success) / every ``mirror.gate_outcome`` (type, success) / the gate-relevant run events (candidate gates passed,
  static analysis, approval, terminal regression, ...), each with the digest of its normalized payload and content
  (``tests/state_machine/_kernel_golden.py``'s ``Normalizer``). Goldens ``tests/golden/gates/engine_*.json``.
- **Attempt level** (the gate functions called through ``kriya.workflow.attempt`` by the names the move keeps): a
  scripted validator class patched at its source (``kriya.tools.validate.PolymorphicValidator``, which the gate bodies
  import at call time) records every validator built (its constructor arguments), every gate it ran (arguments, the
  JAVA_HOME override and the tree binding in force) and answers from a script; the gate outcomes, run events, the
  raised failure and every collaborator call (self-correction loops, the run-verification judge, the
  approve-and-execute and managed-service seams, the suite judgment's scope, spec compliance) are recorded. Goldens
  ``tests/golden/gates/attempt_*.json``.
- **Unit pins**: the JVM toolchain preflight (OI-28: the mismatch warning, the JAVA_HOME override applied to every
  attempt's validator, ``toolchain_checked`` set once and never re-run), the verification-tree binding (what it binds,
  ``candidate.change``, the content-mismatch stop), the deferred gates' order, and the static-analysis /
  commit-binding ordering (SLICE-PLAN E8's named mutant; lesson 5: no ordering test existed).

S7 (final containment contract) re-recorded 9 engine goldens ONCE: the only difference is the content digest of the ``mirror.gate_outcome`` blobs, and the content-level
diff against the pre-S7 tree (19 records) is exactly two things - an outcome carries ``egress`` (the network the contained run was granted: denied, by the containment
profile) and a test outcome no longer carries the host rlimit ``resources`` evidence (it exists only on the host path). The ``egress.backend`` value is the pytest stand-in's
class name (these goldens pin the gate sequence, not isolation). No gate order, verdict, type or attempt count changed.

Every golden was WRITTEN from 74f01ac (``KRIYA_WRITE_GATE_GOLDEN=1``) and is only ever compared afterwards; the forms
are byte-identical under PYTHONHASHSEED 0/1/2/3/5/7/11/13, basetemps of different lengths and a git-archive copy at
another path. The cases added after the first pre-move mutant pass (118 mutants of the to-be-moved code: 13 survived)
pin what those survivors changed: the compile environment stop, the passing gates' containment evidence, the target
test chosen from the failure context, the FS-1A test-delta check, migration scope through pom.xml, the repaired run's
output as the accepted test evidence, a non-pom XML file, an unresolved migration, the established entrypoint of the
pre-review corrections, a staged file missing from the worktree, the immediate path's POM gate, the verification-only
compile's established-only files.
"""
import asyncio
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import developer_double, strict_config

from kriya.agents.contracts import AUTHORITATIVE_GOAL_SECTION_HEADER, PLANNED_IMPLEMENTATION_SECTION_HEADER
from kriya.core.kernel import Kernel
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow import attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.file_integrity import raw_digest
from kriya.workflow.state import GenerationState

sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position

GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "gates"
WRITE_ENV = "KRIYA_WRITE_GATE_GOLDEN"
LONG_TEXT = 240   # a string longer than this is pinned by digest + length


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
    """Long free text (gate output, file content) as digest + length; everything else unchanged."""
    if isinstance(value, str) and len(value) > LONG_TEXT:
        return f"{kg.digest(value.encode('utf-8'))}+{len(value)}"
    if isinstance(value, dict):
        return {str(k): _short(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_short(v) for v in value]
    return value


# ============================================================================= engine level

# The run events that belong to the candidate-gate sequence and its boundaries (pre-apply static analysis, the
# approval that releases held gates, the terminal regression and the attempt verdict).
GATE_EVENTS = frozenset({
    "attempt.started", "generation.completed", "candidate.deterministic_transformation", "auxiliary.failed",
    "validation_baseline.candidate_gate_delta", "contract_feedback.evaluated", "spec_compliance.model_advisory",
    "candidate_gates.passed", "static_analysis.result", "approval.decision", "terminal_regression.passed",
    "terminal_regression.failed", "attempt.passed", "attempt.failed", "unit.no_change_proposed",
    "unit.planned_files_delivered_unchanged",
})
SEQUENCE_KINDS = frozenset({"attempt.opened", "candidate.change", "gate.result", "mirror.gate_outcome",
                            "attempt.closed", "attempt.concluded"})


def _norm_for(outcome: "kg.Outcome", tmp_path: Path) -> "kg.Normalizer":
    from kriya.context.workspace_identity import workspace_identity
    from kriya.core.state_paths import ENV_STATE_DIR

    state_dir = os.environ[ENV_STATE_DIR]
    return kg.Normalizer({
        os.path.realpath(outcome.workspace): "workspace", outcome.workspace: "workspace",
        workspace_identity(outcome.workspace): "workspace_id",
        os.path.realpath(state_dir): "state_dir", state_dir: "state_dir",
        os.path.realpath(str(tmp_path)): "tmp", str(tmp_path): "tmp", **outcome.literals,
    }, outcome.volatile_keys, outcome.volatile_sizes)


def _blob_digest(run: Any, record: Dict[str, Any], name: str, norm: "kg.Normalizer") -> Optional[str]:
    ref = (record.get("blobs") or {}).get(name)
    return kg.digest(norm.content(run.blob(ref)).encode("utf-8")) if ref else None


def gate_sequence(norm: "kg.Normalizer") -> List[Dict[str, Any]]:
    """The ordered gate sequence of every attempt-evidence store the run wrote (store order as the kernel goldens)."""
    from kriya.core.attempt_evidence import reader
    from kriya.core.state_paths import ENV_STATE_DIR

    state_dir = os.environ[ENV_STATE_DIR]
    stores = []
    for run_id in reader.list_runs(state_dir):
        run = reader.open_run(state_dir, run_id)
        records = list(run.records())
        stores.append((records[0].get("t_wall", "") if records else "", records[0].get("t_mono_ms", 0) if records else 0,
                       run_id, run, records))
    rows: List[Dict[str, Any]] = []
    for _wall, _mono, run_id, run, records in sorted(stores, key=lambda s: (s[0], s[1], s[2])):
        rows.append({"store": norm.text(run_id)})
        for record in records:
            kind, payload = record["kind"], record.get("payload") or {}
            if kind == "mirror.event":
                if payload.get("kind") not in GATE_EVENTS:
                    continue
                rows.append({"event": payload.get("kind"), "attempt": payload.get("attempt"),
                             "source": payload.get("source"), "content": _blob_digest(run, record, "event", norm)})
                continue
            if kind not in SEQUENCE_KINDS:
                continue
            row: Dict[str, Any] = {"kind": kind, "attempt": record.get("attempt_number"),
                                   "payload": kg.digest(norm.payload(kind, payload))}
            if kind == "gate.result":
                row.update(stage=payload.get("stage"), gate=payload.get("gate"), success=payload.get("success"))
            elif kind == "mirror.gate_outcome":
                row.update(type=payload.get("type"), success=payload.get("success"),
                           content=_blob_digest(run, record, "outcome", norm))
            elif kind == "candidate.change":
                row.update(path=payload.get("path"), decision=payload.get("decision"),
                           unchanged=payload.get("unchanged"))
            rows.append(row)
    return rows


def _result_fields(result: Any, norm: "kg.Normalizer") -> Dict[str, Any]:
    legacy = getattr(result, "legacy_result", result) or {}
    keys = ("quality_gates_passed", "candidate_gates_passed", "terminal_regression_passed", "overall_attempt_passed",
            "failure_category", "toolchain_warning")
    return {key: norm.value(_short(legacy.get(key))) for key in keys if key in legacy}


def _engine_form(outcome: "kg.Outcome", tmp_path: Path, **extra: Any) -> Dict[str, Any]:
    norm = _norm_for(outcome, tmp_path)
    form = {"sequence": gate_sequence(norm), "result": _result_fields(outcome.result, norm)}
    form.update({key: norm.value(value) for key, value in extra.items()})
    return form


def _autonomous(cfg: Any) -> None:
    cfg.autonomy.mode = "guardrails"


SYNTAX_ERROR = kg.BASE + "\n\ndef mul(a, b)\n    return a * b\n"


def _first_then_good(first: str) -> Callable[[str, int, str], str]:
    return lambda model, n, text: first if n == 1 else kg.GOOD


ENGINE_SCENARIOS: Dict[str, Callable[[Path], "kg.Outcome"]] = {
    # the executing gates run inside run_attempt (autonomous mode: no source review to wait for)
    "immediate_success": lambda tmp: kg._apply_run(tmp, configure=_autonomous),
    # human-in-the-loop with an approver: the executing gates are held until the source is approved
    "deferred_success": lambda tmp: kg._apply_run(tmp),
    "immediate_test_failure_then_success": lambda tmp: kg._apply_run(
        tmp, configure=_autonomous, developer=_first_then_good(kg.BROKEN)),
    "deferred_test_failure_then_success": lambda tmp: kg._apply_run(tmp, developer=_first_then_good(kg.BROKEN)),
    "immediate_compile_failure_then_success": lambda tmp: kg._apply_run(
        tmp, configure=_autonomous, developer=_first_then_good(SYNTAX_ERROR)),
    "deferred_compile_failure_then_success": lambda tmp: kg._apply_run(tmp, developer=_first_then_good(SYNTAX_ERROR)),
}


@pytest.mark.parametrize("name", sorted(ENGINE_SCENARIOS))
def test_engine_gate_sequence(name, tmp_path, monkeypatch):
    for key, value in kg.GIT_ENV.items():
        monkeypatch.setenv(key, value)
    outcome = ENGINE_SCENARIOS[name](tmp_path)
    _compare(f"engine_{name}", _engine_form(outcome, tmp_path))


def _static_analysis_outcome(tmp_path: Path, *, autonomous: bool, blocked: bool) -> "kg.Outcome":
    from _fake_static_analysis import FakeRegistration

    from kriya.config.config import StaticAnalysisConfig

    def configure(cfg):
        cfg.static_analysis = StaticAnalysisConfig(enabled=True, provider="fake")
        if autonomous:
            _autonomous(cfg)
    developer = (lambda m, n, t: kg.GOOD + "# BAD_HIGH\n") if blocked else (lambda m, n, t: kg.GOOD)
    with FakeRegistration(None), patch.dict(os.environ, {"KRIYA_STATIC_ANALYSIS_HOME": str(tmp_path / "waivers")}):
        outcome = kg._apply_run(tmp_path, developer=developer, configure=configure)
    legacy = getattr(outcome.result, "legacy_result", None) or {}
    evidence_digest = (legacy.get("static_analysis") or {}).get("evidence_digest")
    if evidence_digest:   # MEASURED volatile in the kernel goldens: it covers the evidence's tmp paths
        outcome.literals[evidence_digest] = "sa_evidence"
    return outcome


@pytest.mark.parametrize("autonomous", [True, False], ids=["immediate", "deferred"])
@pytest.mark.parametrize("blocked", [True, False], ids=["blocked", "passed"])
def test_engine_static_analysis_gate_sequence(tmp_path, monkeypatch, autonomous, blocked):
    for key, value in kg.GIT_ENV.items():
        monkeypatch.setenv(key, value)
    outcome = _static_analysis_outcome(tmp_path, autonomous=autonomous, blocked=blocked)
    name = f"engine_static_analysis_{'blocked' if blocked else 'passed'}_{'immediate' if autonomous else 'deferred'}"
    _compare(name, _engine_form(outcome, tmp_path))


def _positions(sequence: List[Dict[str, Any]]) -> List[str]:
    out = []
    for row in sequence:
        if "event" in row:
            out.append(row["event"])
        elif row.get("kind") == "gate.result":
            out.append(f"gate.result:{row['stage']}:{row['gate']}")
        elif row.get("kind") == "mirror.gate_outcome":
            out.append(f"gate_outcome:{row['type']}")
        elif "kind" in row:
            out.append(row["kind"])
    return out


@pytest.mark.parametrize("autonomous", [True, False], ids=["immediate", "deferred"])
def test_static_analysis_judges_the_bound_candidate_before_the_approval_and_the_commit(tmp_path, monkeypatch,
                                                                                         autonomous):
    """SLICE-PLAN E8's ordering pin (lesson 5): the PRD-031A static-analysis gate runs on the candidate the gates froze
    (after ``candidate.change`` and, on the immediate path, after the candidate gates passed), BEFORE the approval gate
    and the terminal regression, and the terminal commit names its evidence. On the held path the executing gates run
    only after the approval, so they come after the static-analysis result too."""
    from kriya.control.persistence import list_run_records

    for key, value in kg.GIT_ENV.items():
        monkeypatch.setenv(key, value)
    outcome = _static_analysis_outcome(tmp_path, autonomous=autonomous, blocked=False)
    order = _positions(gate_sequence(_norm_for(outcome, tmp_path)))
    static = order.index("static_analysis.result")
    assert order.index("candidate.change") < static
    assert static < order.index("terminal_regression.passed")
    compile_gates = [i for i, kind in enumerate(order) if kind == "gate.result:validator:compile"]
    assert compile_gates
    if autonomous:
        assert max(compile_gates) < order.index("candidate_gates.passed") < static
        assert "approval.decision" not in order[:static]
    else:
        assert static < order.index("approval.decision") < min(compile_gates)
        assert order.index("candidate_gates.passed") > min(compile_gates)
    legacy = getattr(outcome.result, "legacy_result", None) or {}
    assert legacy["quality_gates_passed"] is True
    evidence_id = f"static_analysis:PASS:{legacy['static_analysis']['evidence_digest']}"
    assert any(evidence_id in record.verification_evidence_ids for record in list_run_records(outcome.workspace))


# ----------------------------------------------------------------------------- JVM toolchain (OI-28)

POM = ("<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n  <modelVersion>4.0.0</modelVersion>\n"
       "  <groupId>org.example</groupId>\n  <artifactId>app</artifactId>\n  <version>1.0</version>\n</project>\n")
APP = "src/main/java/org/example/App.java"
APP_TEST = "src/test/java/org/example/AppTest.java"
APP_BASE = "package org.example;\n\npublic class App {\n    public static int add(int a, int b) { return a + b; }\n}\n"
MUL_LINE = "    public static int mul(int a, int b) { return a * b; }"
MUL_BROKEN_LINE = MUL_LINE.replace("a * b", "a * c")
APP_GOOD = APP_BASE.replace("}\n}\n", f"}}\n{MUL_LINE}\n}}\n")
APP_BROKEN = APP_GOOD.replace(MUL_LINE, MUL_BROKEN_LINE)
APP_TEST_SOURCE = ("package org.example;\n\nimport org.junit.jupiter.api.Test;\n\nclass AppTest {\n"
                   "    @Test\n    void adds() { org.junit.jupiter.api.Assertions.assertEquals(3, App.add(1, 2)); }\n}\n")
POM_NAMED = POM.replace("<version>1.0</version>", "<version>1.0</version>\n  <name>app</name>")
JVM_GOAL = "Add a static method mul(a, b) returning a * b to App.java, targeting Java 17."
JDK17 = "/opt/jdk-17"


class _MavenOracle:
    """Replaces ``PolymorphicValidator._run_maven_cmd`` (the mvn process only, the documented patch point): every gate
    still runs through its ``_verification_gate`` wrapper (tree checks, ``gate.result``). Records each invocation with
    the JAVA_HOME the validator would run it under."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, validator, goals, cwd, *args, **kwargs):
        source = (Path(cwd) / APP).read_text() if (Path(cwd) / APP).is_file() else ""
        broken = "a * c" in source
        self.calls.append({"goals": list(goals), "java_home_override": validator.java_home_override,
                           "stack": validator.stack, "broken": broken})
        if goals and goals[0] in ("clean", "compile") and broken:
            return {"returncode": 1, "stdout": f"[ERROR] {APP}:[5,73] cannot find symbol\n  symbol: variable c\n"
                                               "[INFO] BUILD FAILURE\n", "stderr": "", "timed_out": False}
        if goals and goals[0] in ("clean", "compile"):   # a successful build leaves class files (validate.py checks)
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


def _jvm_run(tmp_path: Path, *, autonomous: bool, target: str = APP, oracle: Any = None) -> Dict[str, Any]:
    """A Maven project through the real legacy pipeline: 'java' resolves to JDK 17 and 'mvn' to JDK 26 (the
    toolchain probe scripted), the goal states Java 17, the first Developer answer does not compile."""
    from _scripted_run import engine, git_workspace

    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow.workflow_controller import WorkflowController

    workspace = tmp_path / "ws"
    workspace.mkdir()
    for rel, text in ((APP, APP_BASE), (APP_TEST, APP_TEST_SOURCE)):
        (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / rel).write_text(text)
    git_workspace(workspace, {"pom.xml": POM, ".gitignore": "target/\n"})

    def configure(cfg):
        cfg.paths.skills = str(tmp_path / "skills")
        cfg.paths.memory = str(tmp_path / "memory")
        cfg.skills.load_global = False
        cfg.skills.load_cwd = False
        cfg.knowledge.offline_mode = True
        if autonomous:
            _autonomous(cfg)
    eng, llm, _cfg = engine(chain=False, configure=configure)
    developer_calls: List[int] = []

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        from _protocol_responses import sentinel, wants_structured

        system_prompt = system_prompt or ""
        if system_prompt.startswith("You are the Kriya Planner Agent"):
            return "Step 1: add mul to App.java"
        if system_prompt.startswith("You are the Kriya Architect Agent"):
            return json.dumps({"files": [target]})
        if not system_prompt.startswith(("You are the Kriya Developer Agent", "You are the Kriya File List Planner")):
            return json.dumps({"verdict": "PASS", "requirements": []})
        developer_calls.append(1)
        if target == "pom.xml":   # the candidate is the pom itself: one answer, which validates and builds
            return sentinel("pom.xml", analysis="name the project", content=POM_NAMED)
        content = APP_BROKEN if len(developer_calls) == 1 else APP_GOOD
        if wants_structured(system_prompt) and '<<<KRIYA:EDIT path=' in system_prompt and len(developer_calls) > 1:
            return sentinel(APP, analysis="use b", edits=[(MUL_BROKEN_LINE, MUL_LINE)])
        if wants_structured(system_prompt):
            return sentinel(APP, analysis="add mul", content=content)
        if "FILE CONTENT:" in user_prompt:
            return f"FIX ANALYSIS: add mul\nFILE CONTENT:\n{content}"
        return json.dumps([{"filepath": APP, "content": content}])
    llm.complete = complete
    oracle = oracle if oracle is not None else _MavenOracle()   # Slice F: a case may script what the mvn process answers
    probes: List[str] = []
    homes: List[str] = []

    def probe():
        probes.append("check_java_toolchain")
        return {"java_found": True, "java_version": "17", "mvn_found": True, "mvn_java_version": "26",
                "mismatch": True}

    def home(version):
        homes.append(version)
        return JDK17
    with patch.object(PolymorphicValidator, "_run_maven_cmd", lambda self, goals, cwd, *a, **k: oracle(self, goals, cwd, *a, **k)), \
            patch("kriya.tools.validate.check_java_toolchain", side_effect=probe), \
            patch("kriya.workflow.toolchain._resolve_jdk_home_for_version", side_effect=home):
        result = asyncio.run(WorkflowController(eng).execute(
            migration_mode="legacy", goal=JVM_GOAL if target == APP else "Name the project app in pom.xml.",
            workspace_path=str(workspace),
            approval_callback=MagicMock(return_value=True)))
    return {"outcome": kg.Outcome(result=result, workspace=str(workspace), cfg=_cfg,
                                  volatile_keys=("effective_config_fingerprint",)),
            "maven": oracle.calls, "probes": probes, "homes": homes, "developer_calls": len(developer_calls)}


@pytest.mark.parametrize("autonomous", [True, False], ids=["immediate", "deferred"])
def test_engine_a_written_pom_is_validated_before_it_is_built(tmp_path, monkeypatch, autonomous):
    """The POM validation gate runs on a candidate that wrote pom.xml (``mvn validate`` before the compile gate): inside
    run_attempt on the immediate path, after the approval (first of the held gates) on the deferred one."""
    for key, value in kg.GIT_ENV.items():
        monkeypatch.setenv(key, value)
    run = _jvm_run(tmp_path, autonomous=autonomous, target="pom.xml")
    legacy = getattr(run["outcome"].result, "legacy_result", None) or {}
    assert legacy["quality_gates_passed"] is True
    goals = [call["goals"][0] for call in run["maven"]]
    assert goals.index("validate") < goals.index("clean")
    _compare(f"engine_pom_written_{'immediate' if autonomous else 'deferred'}",
             _engine_form(run["outcome"], tmp_path, maven=run["maven"]))


# ----------------------------------------------------------------------------- the verification-only unit

def test_engine_verification_only_unit_gate_sequence(tmp_path, monkeypatch):
    """A structured enforce run whose second unit is verification-only (DENY_ALL, a declared compile verifier): its
    attempt runs only the declared verifier through VerificationCoordinator (no Developer request, no candidate)."""
    for key, value in kg.GIT_ENV.items():
        monkeypatch.setenv(key, value)
    import _enforce_unit_harness as harness

    run = harness.run_enforce(tmp_path, plan=kg._enforce_plan(verification_unit=True),
                              units={"s1": (kg.ENFORCE_SERVICE, kg.ENFORCE_PRODUCED)},
                              base_files={kg.ENFORCE_SERVICE: kg.ENFORCE_BASE, "shop/__init__.py": ""})
    outcome = kg.Outcome(result=run.result, workspace=str(run.workspace), cfg=None, volatile_keys=kg.ENFORCE_VOLATILE)
    norm = _norm_for(outcome, tmp_path)
    form = {"sequence": gate_sequence(norm),
            "gate_outcomes": [norm.value(_short({k: v for k, v in o.items() if k != "duration_seconds"}))
                              for o in run.gate_outcomes],
            "builds": norm.value(_short([list(b) if isinstance(b, tuple) else b for b in run.builds])),
            "events": [[e.kind, e.attempt, e.source] for e in run.events if e.kind in GATE_EVENTS]}
    _compare("engine_verification_only_unit", form)


# ============================================================================= attempt level

class _Script:
    """What the scripted validator answers per gate (in call order; default: success) and what every validator the
    gates built saw."""

    def __init__(self, *, stack: str = "python", compile=(), tests=(), pom=()):
        self.stack = stack
        self.answers = {"compile": list(compile), "tests": list(tests), "pom": list(pom)}
        self.log: List[Dict[str, Any]] = []
        self.count = 0
        self.binding: Any = None
        self.autonomy: Any = None

    def validator_class(self):
        script = self

        class ScriptedValidator:
            def __init__(self, workspace_path, original_workspace_path=None, autonomy_cfg=None, java_home_override=None,
                         authorized_dependency_removals=None, toolchain_declaration_mutable=False):
                self.index = script.count
                script.count += 1
                self.workspace_path = workspace_path
                self.stack = script.stack
                self.tree_binding = None
                self._java_home_override = java_home_override
                script.log.append({
                    "call": "construct", "validator": self.index, "workspace_path": workspace_path,
                    "original_workspace_path": original_workspace_path,
                    "autonomy_cfg": "the kernel's" if autonomy_cfg is script.autonomy else repr(autonomy_cfg),
                    "java_home_override": java_home_override,
                    "authorized_dependency_removals": sorted(authorized_dependency_removals or ()),
                    "toolchain_declaration_mutable": toolchain_declaration_mutable,
                })

            @property
            def java_home_override(self):
                return self._java_home_override

            @java_home_override.setter
            def java_home_override(self, value):
                script.log.append({"call": "set_java_home_override", "validator": self.index, "value": value})
                self._java_home_override = value

            def _gate(self, gate: str, args: Dict[str, Any]) -> Dict[str, Any]:
                binding = ("the attempt's" if self.tree_binding is not None and self.tree_binding is script.binding
                           else None if self.tree_binding is None else "another")
                script.log.append({"call": gate, "validator": self.index, **args,
                                   "java_home_override": self._java_home_override, "tree_binding": binding})
                answers = script.answers[gate]
                # a passing gate carries containment evidence (execution_evidence keeps it on the gate outcome)
                answer = answers.pop(0) if answers else {"success": True, "output": f"{gate} ok: 3 passed",
                                                         "toolchain_identity": {"image": f"scripted-{gate}"}}
                return dict(answer)

            def run_compile_check(self, files, *, deadline=None):
                return self._gate("compile", {"files": list(files)})

            def run_tests(self, target_test=None, *, module=None):
                return self._gate("tests", {"target_test": target_test})

            def run_pom_validate(self):
                return self._gate("pom", {})

        return ScriptedValidator


class _Binding:
    """The attempt's verification-tree binding as the gates see it: every explicit check is recorded (a real
    validator also checks it around each gate; the scripted one does not)."""

    def __init__(self, script: "_Script"):
        self._script = script

    def check(self, name: str, *args: Any, **kwargs: Any) -> None:
        self._script.log.append({"call": "tree_binding.check", "name": name, "args": list(args), **kwargs})


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=str(repo), check=True,
                   capture_output=True)


CALC = "def add(a, b):\n    return a + b\n"
CALC_NEW = CALC + "\n\ndef mul(a, b):\n    return a * b\n"
TEST_CALC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


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


def _config(**autonomy: Any):
    return strict_config(autonomy={"run_verification_enabled": False, "spec_compliance_enabled": False,
                                   "self_correction_loop_enabled": False, **autonomy})


def _self_correcting():
    """Self-correction on, with values the loop's arguments must be built FROM (not the defaults)."""
    return _config(self_correction_loop_enabled=True, self_correction_loop_max_turns=3,
                   semantic_region_enforcement_required=True)


def _ctx(workspace: str, worktree: str, cfg: Any, **overrides: Any) -> "attempt.AttemptContext":
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None,
                                                 "command_source": "inferred", "success_criteria": ""})
    spec_compliance = AsyncMock()
    spec_compliance.check = AsyncMock(return_value={"compliant": True, "reasoning": "every requirement is met",
                                                    "missing_requirements": [], "likely_files": []})
    defaults = dict(
        goal="Add mul(a, b) to calc.py.", plan="Step 1: add mul", design="Design: calc.py gains mul",
        workspace_path=workspace, worktree_path=worktree, architect_files=["calc.py"], resume_state=None,
        run_id="gate-run", skills_prompt="", learned_rag_context="", matched_files=[], related_files=[],
        ecosystem_invariant_block="", resource_lifecycle_block="", verification_contract_block="",
        recovery_contract_block="", required_files_prompt_block="", required_dependencies_prompt_block="",
        expected_files_upfront=["calc.py"], architect_basename_to_path={"calc.py": "calc.py"}, chain=[],
        targeted_max_retries=3, stream_callback=None, approval_callback=lambda diffs, reason: True,
        active_skills=[], active_skill_rules_snapshot={}, developer=developer_double(), run_verifier=run_verifier,
        spec_compliance=spec_compliance, skill_engine=MagicMock(), kernel=Kernel(config=cfg), max_retries=4,
        web_lookup_query_callback=None, approve_web_lookup=AsyncMock(return_value=False),
        established_files=["lib.py"], grounding_goal="Add mul(a, b) to calc.py (grounding).",
    )
    defaults.update(overrides)
    return attempt.AttemptContext(**defaults)


def _state(*written: str, attempt_number: int = 1) -> GenerationState:
    state = GenerationState()
    state.attempt_number = attempt_number
    state.all_files_written = set(written or ("calc.py",))
    state.files_written = sorted(state.all_files_written)
    return state


SET_ORDERED = frozenset({"files_in_scope", "writable_files"})


class _Seams:
    """The collaborators the gates hand work to, recorded where they are looked up today (on kriya.workflow.attempt
    and the self-correction module) and answered from the case."""

    def __init__(self, norm_value: Callable[[Any], Any]):
        self.calls: List[Dict[str, Any]] = []
        self._value = norm_value

    def record(self, name: str, **fields: Any) -> None:
        # files_in_scope / writable_files of the targeted repair are list(<a set>): their order is the set's
        for key in SET_ORDERED & set(fields):
            if isinstance(fields[key], list):
                fields[key] = sorted(fields[key])
        self.calls.append({"seam": name, **{k: self._value(_short(v)) for k, v in fields.items()}})


def _event_form(event: Any) -> Dict[str, Any]:
    return {"kind": event.kind, "attempt": event.attempt, "source": event.source,
            "authority": getattr(event.authority, "value", event.authority), "message": _short(event.message),
            "details": _short(event.details), "failure_type": event.failure_type, "operation": event.operation}


def run_gates(tmp_path: Path, case: Callable[..., Any], *, script: _Script, files=None, candidate=None,
              cfg=None, ctx_overrides=None, state: Optional[GenerationState] = None,
              self_correction: Any = None, repair: Any = None) -> Dict[str, Any]:
    """Runs ``case(state, ctx, script)`` (a call of the gate functions through kriya.workflow.attempt) and returns the
    recorded form: validator log, gate outcomes, run events, seam calls, the raised failure or 'returned'.
    ``ctx_overrides`` may be a callable of the workspace path."""
    files = REPO if files is None else files
    candidate = {"calc.py": CALC_NEW} if candidate is None else candidate
    workspace, worktree = _trees(tmp_path, files, candidate)
    cfg = cfg if cfg is not None else _config()
    overrides = ctx_overrides(workspace) if callable(ctx_overrides) else (ctx_overrides or {})
    ctx = _ctx(workspace, worktree, cfg, **overrides)
    state = state if state is not None else _state(*candidate)
    for rel in candidate:
        state.candidate_digests.setdefault(rel, raw_digest((Path(worktree) / rel).read_bytes()))
    script.autonomy = cfg.autonomy
    norm = kg.Normalizer({os.path.realpath(worktree): "wt", worktree: "wt", os.path.realpath(workspace): "ws",
                          workspace: "ws", os.path.realpath(str(tmp_path)): "tmp", str(tmp_path): "tmp"})
    seams = _Seams(norm.value)

    async def self_correction_loop(**kwargs):
        seams.record("run_self_correction_loop", **{k: v for k, v in kwargs.items() if k not in ("llm", "validator")},
                     llm_is_the_developers=kwargs.get("llm") is ctx.developer.llm,
                     validator=getattr(kwargs.get("validator"), "index", None))
        return self_correction

    async def repair_loop(**kwargs):
        seams.record("run_repair_loop", **{k: v for k, v in kwargs.items() if k not in ("llm", "validator")},
                     llm_is_the_developers=kwargs.get("llm") is ctx.developer.llm,
                     validator=getattr(kwargs.get("validator"), "index", None))
        return repair

    async def approve_and_execute(state_arg, ctx_arg, validator, active_code_context, judgment, autonomy_cfg):
        seams.record("_approve_and_execute_runtime_verification", validator=validator.index,
                     same_state=state_arg is state, same_ctx=ctx_arg is ctx,
                     active_code_context=active_code_context, judgment=judgment,
                     autonomy_cfg_is_the_kernels=autonomy_cfg is cfg.autonomy)

    async def managed_service(state_arg, ctx_arg, judgment, known_files, validator):
        seams.record("_execute_managed_service_verification", validator=validator.index, same_state=state_arg is state,
                     same_ctx=ctx_arg is ctx, judgment=judgment, known_files=known_files)
    real_judge = attempt._judge_candidate_suite

    def judge_suite(state_arg, ctx_arg, result, scope):
        seams.record("_judge_candidate_suite", scope=scope, result=result)
        return real_judge(state_arg, ctx_arg, result, scope)
    outcome: Any = "returned"
    with patch("kriya.tools.validate.PolymorphicValidator", script.validator_class()), \
            patch("kriya.workflow.self_correction.run_self_correction_loop", side_effect=self_correction_loop), \
            patch("kriya.workflow.self_correction.run_repair_loop", side_effect=repair_loop), \
            patch.object(attempt, "_approve_and_execute_runtime_verification", side_effect=approve_and_execute), \
            patch.object(attempt, "_execute_managed_service_verification", side_effect=managed_service), \
            patch.object(attempt, "_judge_candidate_suite", side_effect=judge_suite):
        try:
            asyncio.run(case(state, ctx, script))
        except QualityGateFailure as error:
            failure = error.failure
            outcome = {"raised": "QualityGateFailure", "type": failure.type, "message": _short(failure.message),
                       "raw_output": _short(failure.raw_output), "likely_files": failure.likely_files,
                       "authoritative_files": getattr(failure, "authoritative_files", None),
                       "diagnostics": _short(failure.diagnostics),
                       "self_correction_attempt": _short(getattr(failure, "self_correction_attempt", None))}
    return norm.value({
        "outcome": outcome, "validators": script.log, "seams": seams.calls,
        "gate_outcomes": [_short(o) for o in state.gate_outcomes],
        "events": [_event_form(e) for e in state.run_events],
        "state": {"candidate_gates_succeeded": state.candidate_gates_succeeded,
                  "toolchain_checked": state.toolchain_checked, "toolchain_warning": state.toolchain_warning,
                  "java_home_override": state.java_home_override,
                  "validated_file_revisions": _short(state.validated_file_revisions),
                  "plan_scope_conflict": state.plan_scope_conflict,
                  "validator_timings": [[t["kind"], t["success"], isinstance(t["duration_seconds"], float)]
                                        for t in state.validator_timings],
                  "cached_run_verification_judgment": _short(state.cached_run_verification_judgment),
                  "cached_run_verification_basis_hash": state.cached_run_verification_basis_hash is not None,
                  "api_contract_recovery": (None if state.api_contract_recovery is None
                                            else state.api_contract_recovery.phase.value),
                  "ledger": (_short(ctx.obligation_ledger.to_snapshot()) if ctx.obligation_ledger is not None
                             else None)},
    })


async def _executing(state, ctx, script, *, skipping=False, active="ACTIVE CODE CONTEXT"):
    script.binding = _Binding(script)
    await attempt._run_executing_candidate_gates(state, ctx, skipping, script.binding, active)


# a Python syntax error in the candidate, a javac locator naming a REAL repository file the run never tracked
# (src/Sibling.java) and one naming no file at all (Ghost.java): the message gains both notes
FAIL_COMPILE = {"success": False, "output": ("Syntax error in calc.py line 4: invalid syntax\n"
                                             "src/Sibling.java:[3,1] error: cannot find symbol\n"
                                             "Ghost.java:[9,5] error: cannot find symbol\n")}
REPO = {"calc.py": CALC, "lib.py": "LIB = 1\n", "test_calc.py": TEST_CALC, "src/Sibling.java": "class Sibling {}\n"}
CROSS_PACKAGE = {"success": False, "output": ("src/main/java/com/example/App.java:[4,9] error: cannot find symbol\n"
                                              "  symbol:   class Protocol\n  location: class com.example.App\n")}
JAVA_APP = "package com.example;\n\npublic class App {\n    Protocol protocol = new Protocol();\n}\n"
PROCESS_TERMINATED = {"success": False, "output": ("[ERROR] ExecutionException The forked VM terminated without "
                                                   "properly saying goodbye. VM crash or System.exit called?\n")}
GSON_POM = ("<project><dependencies>\n<dependency><groupId>com.google.code.gson</groupId><artifactId>gson</artifactId>"
            "<version>2.11.0</version></dependency>\n<dependency><groupId>com.fasterxml.jackson.core</groupId>"
            "<artifactId>jackson-databind</artifactId><version>2.17.2</version></dependency>\n</dependencies></project>\n")
GSON_SERVICE = ("package com.example;\nimport com.google.gson.Gson;\npublic class JsonService {\n"
                " private final Gson gson=new Gson();\n public String serialize(Object c){ return gson.toJson(c); }\n}\n")
MIGRATION_GOAL = ("Replace the existing JSON serialization library with the JSON library already approved for this "
                  "repository.")
JSON_SERVICE = "src/main/java/com/example/JsonService.java"
ENV_STOP = {"success": False, "output": "gradle wrapper could not start", "environment_reason_code": "GRADLE_WRAPPER_UNAVAILABLE"}
ZERO_TESTS = {"success": True, "output": "collected 0 items\n\nno tests ran in 0.01s"}
FAIL_TESTS = {"success": False, "output": "FAILED test_calc.py::test_add - assert 4 == 3\n1 failed in 0.02s"}
JUDGE_RUN = {"should_run": True, "run_commands": [["python", "calc.py"]], "command_source": "inferred",
             "success_criteria": "prints 6"}
JUDGE_APP = {**JUDGE_RUN, "run_commands": [["python", "app.py"]]}
TESTS_GOAL = "Add mul(a, b) to calc.py with unit tests."
WITH_TEST = {"calc.py": CALC_NEW, "test_mul.py": "from calc import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"}
# two written test modules: no single target, the gate runs the suite
WITH_TESTS = {**WITH_TEST, "test_div.py": "def test_div():\n    assert 6 / 3 == 2\n"}
MAIN_APP = "public class Main {\n    public static void main(String[] args) { System.out.println(6); }\n}\n"
PY_APP = "from calc import mul\n\nif __name__ == \"__main__\":\n    print(mul(2, 3))\n"


class _Resolved:
    def __init__(self, resolved: bool, turns: int = 2, final_output: str = "fixed output: 3 passed"):
        self.resolved = resolved
        self.turns_used = turns
        self.final_compile_output = final_output
        self.transcript = [{"turn": 1, "action": "patched calc.py"}]
        self.incidents = [{"source": "self_correction", "message": "one turn refused", "type": "edit_refused"}]
        self.scope_conflict_files = [] if resolved else ["outside.py"]


def _with_error(state: GenerationState, error: str) -> GenerationState:
    state.error_context = error
    return state


def _with_originals(state: GenerationState) -> GenerationState:
    state.all_original_contents = {"calc.py": CALC}
    return state


@dataclasses.dataclass
class _Region:
    relpath: str = "calc.py"
    member_key: str = "calc.py::mul"


def _violated_ledger():
    """A run ledger where s1's process-boundary obligation is already VIOLATED (a recurrence on the next one)."""
    from kriya.workflow.obligations import (
        ObligationAuthority,
        ObligationKind,
        ObligationLedger,
        ObligationRecord,
        ObligationStatus,
    )

    ledger = ObligationLedger()
    ledger.record(ObligationRecord(
        id=attempt._process_boundary_obligation_id("s1"), kind=ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY,
        status=ObligationStatus.VIOLATED, authority=ObligationAuthority.DETERMINISTIC, description="prior",
        source="test", revision=1, evidence={}, owner_subtask_id="s1", terminal_required=False))
    return ledger


def _migration_ctx(workspace: str) -> Dict[str, Any]:
    from kriya.workflow.migration import resolve_migration_resolution

    return dict(goal="Modify JsonService.java to use the new JSON library", grounding_goal=MIGRATION_GOAL,
                architect_files=[JSON_SERVICE], established_files=[],
                migration_resolution=resolve_migration_resolution(MIGRATION_GOAL, workspace))


SELF_CORRECTION_CTX = dict(authorized_semantic_regions=[_Region()])
TARGETED_STATE = lambda: _with_error(_state("calc.py", "test_mul.py"), "FAILED test_mul.py::test_mul - boom")  # noqa: E731
BOUNDARY_CTX = lambda workspace: dict(obligation_ledger=_violated_ledger(), current_subtask_id="s1")  # noqa: E731

ATTEMPT_CASES: Dict[str, Dict[str, Any]] = {
    # ---- compile gate
    "compile_failure": dict(script=lambda: _Script(compile=[FAIL_COMPILE])),
    "compile_failure_cross_package": dict(
        script=lambda: _Script(stack="java", compile=[CROSS_PACKAGE]),
        files={"Protocol.java": "public class Protocol {}\n", "pom.xml": POM},
        candidate={"src/main/java/com/example/App.java": JAVA_APP},
        ctx_overrides=dict(established_files=["Protocol.java"])),
    "compile_failure_self_correction_resolved": dict(
        script=lambda: _Script(compile=[FAIL_COMPILE]), cfg=_self_correcting, self_correction=_Resolved(True),
        state=lambda: _with_originals(_state("calc.py")), ctx_overrides=SELF_CORRECTION_CTX),
    "compile_failure_self_correction_unresolved": dict(
        script=lambda: _Script(compile=[FAIL_COMPILE]), cfg=_self_correcting, self_correction=_Resolved(False),
        state=lambda: _with_originals(_state("calc.py")), ctx_overrides=SELF_CORRECTION_CTX),
    "compile_failure_self_correction_deny_all": dict(
        script=lambda: _Script(compile=[FAIL_COMPILE]), cfg=_self_correcting, self_correction=_Resolved(False),
        ctx_overrides=dict(write_scope_mode=WriteScopeMode.DENY_ALL)),
    "compile_failure_self_correction_allowlist": dict(
        script=lambda: _Script(compile=[FAIL_COMPILE]), cfg=_self_correcting, self_correction=_Resolved(False),
        ctx_overrides=dict(write_scope_mode=WriteScopeMode.ALLOWLIST, allowed_write_relpaths=["calc.py", "x.py"])),
    "compile_failure_after_source_review_skips_self_correction": dict(
        script=lambda: _Script(compile=[FAIL_COMPILE]), cfg=_self_correcting, self_correction=_Resolved(True),
        ctx_overrides=dict(post_source_review_guard=lambda: True)),
    "compile_environment_stop": dict(script=lambda: _Script(compile=[ENV_STOP])),
    "worktree_changed_since_staging": dict(script=lambda: _Script(), state=lambda: _stale_stage(_state("calc.py"))),
    # ---- test gates
    "targeted_environment_stop": dict(script=lambda: _Script(tests=[ENV_STOP]), candidate=WITH_TEST,
                                      state=TARGETED_STATE),
    "suite_environment_stop": dict(script=lambda: _Script(tests=[ENV_STOP]), candidate=WITH_TESTS),
    "selection_fallback_environment_stop": dict(script=lambda: _Script(tests=[ZERO_TESTS, ENV_STOP]),
                                                candidate=WITH_TEST, state=TARGETED_STATE),
    "no_runnable_tests_no_declared_verification": dict(script=lambda: _Script()),
    "full_suite_passes": dict(script=lambda: _Script(), candidate=WITH_TESTS),
    "full_suite_declared_verification": dict(
        script=lambda: _Script(), ctx_overrides=dict(required_verification=[
            {"type": "tool", "tool_name": "test", "description": "the suite"}])),
    "full_suite_fails": dict(script=lambda: _Script(tests=[FAIL_TESTS]), candidate=WITH_TESTS),
    "full_suite_process_terminated_recurrence": dict(
        script=lambda: _Script(tests=[PROCESS_TERMINATED]), candidate=WITH_TESTS, ctx_overrides=BOUNDARY_CTX),
    "full_suite_passes_after_a_process_boundary_violation": dict(
        script=lambda: _Script(), candidate=WITH_TESTS, ctx_overrides=BOUNDARY_CTX),
    # two written test modules: only the failure context selects one of them (else the suite runs)
    "targeted_test_chosen_from_the_failure_context": dict(
        script=lambda: _Script(), candidate=WITH_TESTS,
        state=lambda: _with_error(_state("calc.py", "test_mul.py", "test_div.py"), "FAILED test_div.py::test_div")),
    "targeted_test_passes": dict(script=lambda: _Script(), candidate=WITH_TEST, state=TARGETED_STATE),
    "targeted_test_passes_after_a_process_boundary_violation": dict(
        script=lambda: _Script(), candidate=WITH_TEST, state=TARGETED_STATE, ctx_overrides=BOUNDARY_CTX),
    "targeted_test_zero_collected_falls_back_to_the_suite": dict(
        script=lambda: _Script(tests=[ZERO_TESTS]), candidate=WITH_TEST, state=TARGETED_STATE),
    "targeted_test_zero_collected_suite_process_terminated": dict(
        script=lambda: _Script(tests=[ZERO_TESTS, PROCESS_TERMINATED]), candidate=WITH_TEST, state=TARGETED_STATE,
        ctx_overrides=BOUNDARY_CTX),
    "targeted_test_zero_collected_suite_fails": dict(
        script=lambda: _Script(tests=[ZERO_TESTS, FAIL_TESTS]), candidate=WITH_TEST, state=TARGETED_STATE),
    "targeted_test_fails": dict(script=lambda: _Script(tests=[FAIL_TESTS]), candidate=WITH_TEST,
                                state=TARGETED_STATE),
    "targeted_test_process_terminated_recurrence": dict(
        script=lambda: _Script(tests=[PROCESS_TERMINATED]), candidate=WITH_TEST, state=TARGETED_STATE,
        ctx_overrides=BOUNDARY_CTX),
    "targeted_test_fails_repair_resolved": dict(
        script=lambda: _Script(tests=[FAIL_TESTS]), cfg=_self_correcting, repair=_Resolved(True),
        candidate=WITH_TEST, state=TARGETED_STATE),
    # the repaired run's own output is the accepted test evidence (here: it ran no test, so an owned test is missing)
    "targeted_test_fails_repair_resolved_output_is_the_accepted_one": dict(
        script=lambda: _Script(tests=[FAIL_TESTS]), cfg=_self_correcting,
        repair=_Resolved(True, final_output=ZERO_TESTS["output"]), candidate=WITH_TEST, state=TARGETED_STATE,
        ctx_overrides=dict(goal="Add mul(a, b) to calc.py.", grounding_goal=TESTS_GOAL)),
    "targeted_test_fails_repair_unresolved": dict(
        script=lambda: _Script(tests=[FAIL_TESTS]), cfg=_self_correcting, repair=_Resolved(False),
        candidate=WITH_TEST, state=TARGETED_STATE,
        ctx_overrides=dict(write_scope_mode=WriteScopeMode.ALLOWLIST, allowed_write_relpaths=["test_mul.py"])),
    "targeted_test_fails_repair_deny_all": dict(
        script=lambda: _Script(tests=[FAIL_TESTS]), cfg=_self_correcting, repair=_Resolved(False),
        candidate=WITH_TEST, state=TARGETED_STATE, ctx_overrides=dict(write_scope_mode=WriteScopeMode.DENY_ALL)),
    "targeted_test_fails_after_source_review_skips_repair": dict(
        script=lambda: _Script(tests=[FAIL_TESTS]), cfg=_self_correcting, repair=_Resolved(True),
        candidate=WITH_TEST, state=TARGETED_STATE, ctx_overrides=dict(post_source_review_guard=lambda: True)),
    "goal_requires_tests_none_written": dict(
        script=lambda: _Script(), ctx_overrides=dict(goal="Add mul(a, b) to calc.py.", grounding_goal=TESTS_GOAL)),
    # one written test module (its likely_files come from iterating the written set: one element keeps them ordered)
    "goal_requires_tests_zero_executed": dict(
        script=lambda: _Script(tests=[ZERO_TESTS, ZERO_TESTS]), candidate=WITH_TEST,
        ctx_overrides=dict(goal="Add mul(a, b) to calc.py.", grounding_goal=TESTS_GOAL)),
    # an owned test passes, but its run carries no structured report: the FS-1A test-delta check takes over
    "goal_requires_tests_test_delta_checked": dict(
        script=lambda: _Script(), candidate=WITH_TEST,
        ctx_overrides=dict(goal="Add mul(a, b) to calc.py.", grounding_goal=TESTS_GOAL)),
    "goal_requires_tests_only_in_the_subtask_goal": dict(
        script=lambda: _Script(), ctx_overrides=dict(goal=TESTS_GOAL, grounding_goal="Add mul(a, b) to calc.py.")),
    # ---- runtime verification block (the judge and the decisions before the approve-and-execute seam, E9's)
    "runtime_required_but_disabled": dict(script=lambda: _Script(),
                                          ctx_overrides=dict(runtime_verification_required=True)),
    "runtime_judged_should_not_run": dict(script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True)),
    "runtime_judged_and_handed_to_approve_and_execute": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True), judge=JUDGE_APP,
        candidate={"calc.py": CALC_NEW, "app.py": PY_APP}),
    "runtime_declined_earlier_skips_the_judge": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True), judge=JUDGE_APP,
        state=lambda: _declined(_state("calc.py"))),
    "runtime_required_declined_earlier": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True), judge=JUDGE_RUN,
        state=lambda: _declined(_state("calc.py")), ctx_overrides=dict(runtime_verification_required=True)),
    "runtime_required_judge_infrastructure_error": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True),
        judge={"should_run": False, "infrastructure_error": "judge unavailable"},
        ctx_overrides=dict(runtime_verification_required=True)),
    "runtime_judge_infrastructure_error_not_required": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True),
        judge={"should_run": False, "run_commands": None, "command_source": "inferred",
               "infrastructure_error": "judge unavailable"}),
    "runtime_required_judged_should_not_run": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True),
        ctx_overrides=dict(runtime_verification_required=True)),
    "runtime_managed_service_returns_early": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True, spec_compliance_enabled=True),
        judge={**JUDGE_RUN, "execution_mode": "managed_service"}),
    "runtime_invalid_execution_mode": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True),
        judge={**JUDGE_RUN, "execution_mode": "teleport"}),
    "runtime_required_build_only": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True),
        judge={**JUDGE_RUN, "run_commands": [["mvn", "-q", "compile"]]},
        ctx_overrides=dict(runtime_verification_required=True)),
    "runtime_build_only_not_required": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True),
        judge={**JUDGE_RUN, "run_commands": [["mvn", "-q", "compile"]]}),
    "runtime_ungrounded_goal_explicit_command_downgraded": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True),
        judge={**JUDGE_APP, "command_source": "goal_explicit"}, candidate={"calc.py": CALC_NEW, "app.py": PY_APP}),
    "runtime_judgment_cached_across_attempts": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True), judge=JUDGE_APP, twice=True,
        candidate={"calc.py": CALC_NEW, "app.py": PY_APP}),
    "runtime_judgment_invalidated_when_the_basis_changes": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True), judge=JUDGE_APP, twice=True,
        candidate={"calc.py": CALC_NEW, "app.py": PY_APP},
        between=lambda state, ctx: (Path(ctx.worktree_path) / "lib.py").write_text("LIB = 2\n")),
    "runtime_java_entrypoint_grounded": dict(
        script=lambda: _Script(stack="java"), cfg=lambda: _config(run_verification_enabled=True),
        judge={**JUDGE_RUN, "run_commands": [["java", "Main"]]}, files={"Util.java": "class Util {}\n"},
        candidate={"Main.java": MAIN_APP}, ctx_overrides=dict(established_files=["Util.java"])),
    "runtime_java_with_a_pom_is_not_regrounded": dict(
        script=lambda: _Script(stack="java"), cfg=lambda: _config(run_verification_enabled=True),
        judge={**JUDGE_RUN, "run_commands": [["java", "Main"]]}, files={"Util.java": "class Util {}\n", "pom.xml": POM},
        candidate={"Main.java": MAIN_APP}, ctx_overrides=dict(established_files=["Util.java"])),
    "runtime_java_no_entrypoint_does_not_run": dict(
        script=lambda: _Script(stack="java"), cfg=lambda: _config(run_verification_enabled=True),
        judge={**JUDGE_RUN, "run_commands": [["java", "ProtocolParserTest"]]}, files={"Util.java": "class Util {}\n"},
        candidate={"Protocol.java": "public class Protocol {}\n"}, ctx_overrides=dict(established_files=[])),
    "runtime_python_target_substituted": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True),
        judge={**JUDGE_RUN, "run_commands": [["python", "test_calc.py"]]}, candidate={"calc.py": CALC_NEW,
                                                                                     "app.py": PY_APP}),
    "runtime_python_target_invalid_does_not_run": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True),
        judge={**JUDGE_RUN, "run_commands": [["python", "test_calc.py"]]}),
    "runtime_advisory_only_for_a_subtask_without_the_obligation": dict(
        script=lambda: _Script(), cfg=lambda: _config(run_verification_enabled=True), judge=JUDGE_APP,
        candidate={"calc.py": CALC_NEW, "app.py": PY_APP},
        ctx_overrides=lambda workspace: dict(structured_plan=_plan_without_runtime(), current_subtask_id="s1")),
    # ---- migration completion
    "migration_incomplete": dict(
        script=lambda: _Script(stack="java"), files={"pom.xml": GSON_POM, JSON_SERVICE: GSON_SERVICE},
        candidate={JSON_SERVICE: GSON_SERVICE.replace("serialize", "toJson")}, ctx_overrides=_migration_ctx,
        cfg=lambda: _config(spec_compliance_enabled=True)),
    "migration_incomplete_owned_through_the_pom": dict(
        script=lambda: _Script(stack="java"), files={"pom.xml": GSON_POM, JSON_SERVICE: GSON_SERVICE},
        candidate={"pom.xml": GSON_POM.replace("2.17.2", "2.17.3")},
        ctx_overrides=lambda workspace: {**_migration_ctx(workspace), "architect_files": ["pom.xml"]}),
    # ---- spec compliance
    "spec_compliant": dict(script=lambda: _Script(), cfg=lambda: _config(spec_compliance_enabled=True)),
    "spec_compliant_with_ledger": dict(
        script=lambda: _Script(), cfg=lambda: _config(spec_compliance_enabled=True),
        ctx_overrides=lambda workspace: dict(obligation_ledger=_violated_ledger(), current_subtask_id="s1")),
    "spec_model_reports_missing": dict(
        script=lambda: _Script(), cfg=lambda: _config(spec_compliance_enabled=True),
        spec=[{"compliant": False, "reasoning": "mul is missing", "missing_requirements": ["mul(a, b) exists"],
               "likely_files": ["calc.py"]}],
        ctx_overrides=lambda workspace: dict(obligation_ledger=_violated_ledger(), current_subtask_id="s1")),
    "spec_indeterminate_twice": dict(
        script=lambda: _Script(), cfg=lambda: _config(spec_compliance_enabled=True),
        spec=[{"compliant": False, "reasoning": "?", "missing_requirements": [], "likely_files": [],
               "status": "indeterminate"}] * 2),
    "spec_indeterminate_then_compliant": dict(
        script=lambda: _Script(), cfg=lambda: _config(spec_compliance_enabled=True),
        spec=[{"compliant": False, "reasoning": "?", "missing_requirements": [], "likely_files": [],
               "status": "indeterminate"},
              {"compliant": True, "reasoning": "on reflection, compliant", "missing_requirements": [],
               "likely_files": []}]),
    "spec_unknown_strict": dict(
        script=lambda: _Script(), cfg=lambda: _config(spec_compliance_enabled=True),
        spec=[{"compliant": True, "reasoning": "check unavailable", "missing_requirements": [], "likely_files": [],
               "status": "unknown"}], ctx_overrides=dict(strict_spec_compliance=True)),
    "spec_unknown_not_strict": dict(
        script=lambda: _Script(), cfg=lambda: _config(spec_compliance_enabled=True),
        spec=[{"compliant": True, "reasoning": "check unavailable", "missing_requirements": [], "likely_files": [],
               "status": "unknown"}]),
    "spec_planner_only_identifier_suppressed": dict(
        script=lambda: _Script(), cfg=lambda: _config(spec_compliance_enabled=True),
        spec=[{"compliant": False, "reasoning": "protocolVersion field missing",
               "missing_requirements": ["the protocolVersion field exists"], "likely_files": ["calc.py"]}],
        ctx_overrides=dict(goal=(f"{AUTHORITATIVE_GOAL_SECTION_HEADER}\nAdd mul(a, b) to calc.py.\n\n"
                                 f"{PLANNED_IMPLEMENTATION_SECTION_HEADER}\nAlso keep a protocolVersion field.\n"))),
    # ---- the reused-candidate resume: candidate gates skipped, the judging gates still run
    "skipping_candidate_gates": dict(script=lambda: _Script(), cfg=lambda: _config(spec_compliance_enabled=True),
                                     skipping=True),
    "skipping_candidate_gates_with_api_contract_recovery": dict(
        script=lambda: _Script(), skipping=True, state=lambda: _with_recovery(_state("calc.py"))),
}


def _stale_stage(state: GenerationState) -> GenerationState:
    """The authorized writer staged other bytes than the worktree holds (something wrote behind it)."""
    state.candidate_digests = {"calc.py": raw_digest(b"staged bytes\n")}
    return state


def _declined(state: GenerationState) -> GenerationState:
    state.run_verification_declined = True
    return state


def _with_recovery(state: GenerationState) -> GenerationState:
    from kriya.workflow.state import APIContractRecovery, APIContractRecoveryPhase

    recovery = APIContractRecovery.detected([{"symbol": "add"}], ["calc.py"], {"calc.py": "owner"})
    recovery.begin_restoration()
    recovery.owner_contract_restored()
    assert recovery.phase is APIContractRecoveryPhase.REPAIR_BEHAVIOR
    state.api_contract_recovery = recovery
    return state


def _plan_without_runtime():
    from kriya.workflow.plan_schema import EngineeringPlan

    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [{"id": "s1", "description": "add mul", "execution_method": "model",
                      "planned_files": [{"path": "calc.py", "action": "modify"}], "depends_on": [], "provides": ["p1"],
                      "relevant_global_invariant_ids": ["gi1"],
                      "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]}]})


@pytest.mark.parametrize("name", sorted(ATTEMPT_CASES))
def test_attempt_gate_case(name, tmp_path):
    spec = dict(ATTEMPT_CASES[name])
    script = spec.pop("script")()
    cfg = spec.pop("cfg", _config)()
    judge = spec.pop("judge", None)
    spec_answers = spec.pop("spec", None)
    skipping = spec.pop("skipping", False)
    twice = spec.pop("twice", False)
    between = spec.pop("between", None)
    state = spec.pop("state", None)
    state = state() if state is not None else None

    async def case(state_arg, ctx, script_arg):
        if judge is not None:
            ctx.run_verifier.judge = AsyncMock(return_value=judge)
        if spec_answers is not None:
            ctx.spec_compliance.check = AsyncMock(side_effect=list(spec_answers))
        await _executing(state_arg, ctx, script_arg, skipping=skipping)
        if twice:
            if between is not None:
                between(state_arg, ctx)
            state_arg.attempt_number += 1
            await _executing(state_arg, ctx, script_arg, skipping=skipping)
        if judge is not None:
            script_arg.log.append({"call": "judge_calls", "count": ctx.run_verifier.judge.await_count,
                                   "kwargs": [_short(c.kwargs) for c in ctx.run_verifier.judge.await_args_list]})
        if spec_answers is not None or cfg.autonomy.spec_compliance_enabled:
            script_arg.log.append({"call": "spec_checks",
                                   "kwargs": [_short(c.kwargs) for c in ctx.spec_compliance.check.await_args_list]})
    form = run_gates(tmp_path, case, script=script, cfg=cfg, state=state, **spec)
    _compare(f"attempt_{name}", form)


# ----------------------------------------------------------------------------- POM validation and the held gates

POM_FILES = {"pom.xml": POM, "src/main/java/org/example/App.java": APP_BASE}


def test_attempt_pom_validation_gate_failure(tmp_path):
    async def case(state, ctx, script):
        script.binding = _Binding(script)
        attempt._run_pom_validation_gate(state, ctx, script.binding)
    script = _Script(stack="java", pom=[{"success": False, "output": "[ERROR] Non-parseable POM: unexpected <plugin>"}])
    state = _state("pom.xml", "src/main/java/org/example/App.java")
    form = run_gates(tmp_path, case, script=script, files=POM_FILES,
                     candidate={"pom.xml": POM.replace("<version>1.0</version>", "<version>1.1</version>"),
                                "src/main/java/org/example/App.java": APP_GOOD}, state=state)
    _compare("attempt_pom_validation_failure", form)


def test_attempt_pom_validation_gate_skipped_without_a_written_pom(tmp_path):
    async def case(state, ctx, script):
        script.binding = _Binding(script)
        attempt._run_pom_validation_gate(state, ctx, script.binding)
    script = _Script(stack="java")
    form = run_gates(tmp_path, case, script=script, files=POM_FILES,
                     candidate={"src/main/java/org/example/App.java": APP_GOOD},
                     state=_state("src/main/java/org/example/App.java"))
    assert form["validators"] == []
    _compare("attempt_pom_validation_not_written", form)


def test_attempt_pom_validation_gate_validates_only_a_written_pom(tmp_path):
    async def case(state, ctx, script):
        script.binding = _Binding(script)
        attempt._run_pom_validation_gate(state, ctx, script.binding)
    script = _Script(stack="java", pom=[{"success": False, "output": "must not be asked"}])
    form = run_gates(tmp_path, case, script=script, files=POM_FILES,
                     candidate={"config/beans.xml": "<beans/>\n"}, state=_state("config/beans.xml"))
    assert form["validators"] == [] and form["outcome"] == "returned"


def test_attempt_deferred_gates_run_the_pom_gate_then_the_executing_gates(tmp_path):
    """The held half of run_attempt (Gap B): after the approval, the candidate must still be the reviewed one, then
    the POM validation (it was ahead of the other gates), then the executing gates - on one tree binding."""
    async def case(state, ctx, script):
        script.binding = _Binding(script)
        await attempt._run_deferred_executing_gates(state, ctx, False, script.binding, "ACTIVE")
    script = _Script(stack="java")
    state = _state("pom.xml", "src/main/java/org/example/App.java")
    form = run_gates(tmp_path, case, script=script, files=POM_FILES,
                     candidate={"pom.xml": POM, "src/main/java/org/example/App.java": APP_GOOD}, state=state,
                     ctx_overrides=dict(post_source_review_guard=lambda: True, architect_files=[
                         "src/main/java/org/example/App.java"], established_files=[]))
    assert [entry["call"] for entry in form["validators"] if entry["call"] in ("pom", "compile", "tests")][:2] == [
        "pom", "compile"]
    _compare("attempt_deferred_gates", form)


def test_attempt_deferred_gates_refuse_a_candidate_changed_after_the_source_review(tmp_path):
    async def case(state, ctx, script):
        script.binding = _Binding(script)
        await attempt._run_deferred_executing_gates(state, ctx, False, script.binding, "ACTIVE")
    script = _Script(stack="java")
    form = run_gates(tmp_path, case, script=script, files=POM_FILES,
                     candidate={"pom.xml": POM, "src/main/java/org/example/App.java": APP_GOOD},
                     state=_state("pom.xml", "src/main/java/org/example/App.java"),
                     ctx_overrides=dict(post_source_review_guard=lambda: False))
    assert form["validators"] == [] and form["outcome"]["type"] == "verification_infrastructure_failure"
    _compare("attempt_deferred_gates_stale_review", form)


def test_attempt_pre_review_corrections(tmp_path):
    """Held mode: before the binding is taken and the source shown, the candidate's own pom.xml is corrected with the
    same validator/toolchain preflight the executing gates use (one new validator, the preflight, the corrections over
    the written and established files)."""
    async def case(state, ctx, script):
        attempt.apply_pre_review_candidate_corrections(state, ctx)
    script = _Script(stack="java")
    pom = POM.replace("</project>", "  <properties>\n    <exec.mainClass>org.example.Missing</exec.mainClass>\n"
                                    "  </properties>\n</project>")
    files = {"pom.xml": POM, "src/main/java/org/example/App.java": APP_BASE}
    app_main = APP_GOOD.replace("public static int mul", "public static void main(String[] args) {}\n    public static int mul")
    with patch("kriya.tools.validate.check_java_toolchain", return_value={
            "java_found": True, "java_version": "17", "mvn_found": True, "mvn_java_version": "26", "mismatch": True}), \
            patch("kriya.workflow.toolchain._resolve_jdk_home_for_version", return_value=JDK17):
        form = run_gates(tmp_path, case, script=script, files=files,
                         candidate={"pom.xml": pom, "src/main/java/org/example/App.java": app_main},
                         state=_state("pom.xml", "src/main/java/org/example/App.java"),
                         ctx_overrides=dict(goal=JVM_GOAL, established_files=[]))
    wt = tmp_path / "wt" / "pom.xml"
    form["pom_after"] = kg.digest(wt.read_bytes())
    _compare("attempt_pre_review_corrections", form)


def test_attempt_pre_review_corrections_read_the_established_entrypoint(tmp_path):
    """The corrections' known files are the written AND the established ones: the only main() is in an established
    file the candidate did not write, and the candidate's pom names a missing main class."""
    async def case(state, ctx, script):
        attempt.apply_pre_review_candidate_corrections(state, ctx)
    pom = POM.replace("</project>", "  <properties>\n    <exec.mainClass>org.example.Missing</exec.mainClass>\n"
                                    "  </properties>\n</project>")
    app_main = APP_BASE.replace("public static int add", "public static void main(String[] args) {}\n    public static int add")
    files = {"pom.xml": POM, "src/main/java/org/example/App.java": app_main}
    with patch("kriya.tools.validate.check_java_toolchain", return_value=_toolchain(False)):
        form = run_gates(tmp_path, case, script=_Script(stack="java"), files=files, candidate={"pom.xml": pom},
                         state=_state("pom.xml"),
                         ctx_overrides=dict(established_files=["src/main/java/org/example/App.java"]))
    after = (tmp_path / "wt" / "pom.xml").read_text()
    assert "<exec.mainClass>org.example.App</exec.mainClass>" in after
    assert [e["kind"] for e in form["events"]] == ["candidate.deterministic_transformation"]


# ----------------------------------------------------------------------------- the JVM toolchain preflight (OI-28)

def _toolchain(mismatch: bool):
    return {"java_found": True, "java_version": "17", "mvn_found": True,
            "mvn_java_version": "26" if mismatch else "17", "mismatch": mismatch}


@pytest.mark.parametrize("mismatch,goal", [(True, JVM_GOAL), (True, "Add mul to App.java."), (False, JVM_GOAL)],
                         ids=["mismatch_goal_states_17", "mismatch_goal_states_none", "no_mismatch"])
def test_the_toolchain_preflight_runs_once_and_its_override_reaches_every_attempts_validator(tmp_path, mismatch, goal):
    """OI-28: the first candidate-gate validator of a run checks java vs mvn once (``toolchain_checked``); a mismatch
    is a warning on the state; a goal stating the 'java' side's version resolves that JDK's home as the JAVA_HOME
    override; every later attempt's NEW validator gets the same override re-applied without probing again. The
    override is resolved from ``ctx.goal`` (the grounding goal differs here and states no version)."""
    probes: List[str] = []

    def probe():
        probes.append("probe")
        return _toolchain(mismatch)

    async def case(state, ctx, script):
        await _executing(state, ctx, script)
        state.attempt_number += 1
        await _executing(state, ctx, script)
    script = _Script(stack="java")
    files = {"pom.xml": POM, APP: APP_BASE}
    with patch("kriya.tools.validate.check_java_toolchain", side_effect=probe), \
            patch("kriya.workflow.toolchain._resolve_jdk_home_for_version", side_effect=lambda v: f"/opt/jdk-{v}"):
        form = run_gates(tmp_path, case, script=script, files=files, candidate={APP: APP_GOOD}, state=_state(APP),
                         ctx_overrides=dict(goal=goal, grounding_goal="Add mul to App.java (no version stated).",
                                            established_files=[]))
    expected_home = JDK17 if (mismatch and goal == JVM_GOAL) else None
    gates = [entry for entry in form["validators"] if entry["call"] in ("compile", "tests")]
    assert {entry["validator"] for entry in gates} == {0, 1}            # a new validator per attempt
    assert [entry["java_home_override"] for entry in gates] == [expected_home] * len(gates)
    assert form["state"]["toolchain_checked"] is True and form["state"]["java_home_override"] == expected_home
    assert probes == ["probe", "probe"]   # once for the warning, once for the override: on the first attempt only
    assert bool(form["state"]["toolchain_warning"]) is mismatch
    _compare(f"attempt_toolchain_{'mismatch' if mismatch else 'match'}_{'17' if goal == JVM_GOAL else 'none'}", form)


def test_the_toolchain_preflight_is_skipped_for_a_non_java_stack(tmp_path):
    probes: List[str] = []

    async def case(state, ctx, script):
        await _executing(state, ctx, script)
    with patch("kriya.tools.validate.check_java_toolchain", side_effect=lambda: probes.append("probe")), \
            patch("kriya.workflow.toolchain._resolve_jdk_home_for_version", side_effect=AssertionError("no JDK")):
        form = run_gates(tmp_path, case, script=_Script(stack="python"), ctx_overrides=dict(goal=JVM_GOAL))
    assert probes == [] and form["state"]["toolchain_checked"] is True and form["state"]["java_home_override"] is None


# ----------------------------------------------------------------------------- the verification-tree binding

def test_the_verification_tree_binding_binds_the_repository_content_and_the_staged_candidate(tmp_path):
    """FILE-INTEGRITY-CONTRACT-001: the binding is taken over the WORKTREE (its root), binding the repository content
    paths of the WORKSPACE (git-tracked there: neither a worktree-only file nor an untracked workspace file) and the
    attempt's staged candidate paths; it becomes the state's binding; the worktree must hold the staged bytes first."""
    files = {"calc.py": CALC, "lib.py": "LIB = 1\n", "test_calc.py": TEST_CALC}
    workspace, worktree = _trees(tmp_path, files, {"calc.py": CALC_NEW, "extra.py": "X = 1\n"})
    (Path(workspace) / "untracked_in_ws.py").write_text("U = 1\n")   # not repository content (git does not track it)
    (Path(worktree) / "worktree_only.py").write_text("W = 1\n")      # in the worktree, not in the repository
    state = _state("calc.py", "extra.py")
    state.candidate_digests = {"calc.py": raw_digest(CALC_NEW.encode()), "extra.py": raw_digest(b"X = 1\n")}
    ctx = _ctx(workspace, worktree, _config())
    bound = attempt._bind_verification_tree(state, ctx)
    assert state.verification_tree_binding is bound
    assert bound.root == worktree
    assert bound.paths == ("calc.py", "extra.py", "lib.py", "test_calc.py")


def test_the_verification_tree_binding_refuses_bytes_the_writer_did_not_stage(tmp_path):
    workspace, worktree = _trees(tmp_path, {"calc.py": CALC}, {"calc.py": CALC_NEW})
    state = _state("calc.py")
    state.candidate_digests = {"calc.py": raw_digest(b"something else\n")}
    ctx = _ctx(workspace, worktree, _config())
    with pytest.raises(QualityGateFailure) as raised:
        attempt._bind_verification_tree(state, ctx)
    assert raised.value.failure.diagnostics == {"reason_code": "WORKTREE_CONTENT_MISMATCH", "paths": ["calc.py"]}
    assert state.verification_tree_binding is None
    assert [o["type"] for o in state.gate_outcomes] == ["internal_framework_error"]


def test_a_refused_candidate_emits_no_candidate_change_evidence(tmp_path):
    """The worktree check comes BEFORE the candidate.change record: bytes the writer did not stage are refused
    without the evidence stream ever describing them as the frozen candidate (capture on, so a swapped order shows)."""
    workspace, worktree = _trees(tmp_path, {"calc.py": CALC}, {"calc.py": CALC_NEW})
    state = _state("calc.py")
    state.candidate_digests = {"calc.py": raw_digest(b"something else\n")}
    ctx = _ctx(workspace, worktree, _config())
    emitted = []
    with patch("kriya.core.attempt_evidence.scope.capture_mode", return_value="full"), \
            patch("kriya.core.attempt_evidence.scope.emit",
                  side_effect=lambda kind, payload, content=None: emitted.append(kind)), \
            pytest.raises(QualityGateFailure):
        attempt._bind_verification_tree(state, ctx)
    assert emitted == []


def test_a_staged_candidate_file_missing_from_the_worktree_is_refused(tmp_path):
    workspace, worktree = _trees(tmp_path, {"calc.py": CALC}, {"calc.py": CALC_NEW})
    state = _state("calc.py", "gone.py")
    state.candidate_digests = {"calc.py": raw_digest(CALC_NEW.encode()), "gone.py": raw_digest(b"G = 1\n")}
    with pytest.raises(QualityGateFailure) as raised:
        attempt._require_worktree_matches_candidate(state, _ctx(workspace, worktree, _config()))
    assert raised.value.failure.diagnostics["paths"] == ["gone.py"]


def test_the_candidate_change_record_is_built_from_the_original_and_the_worktree_bytes(tmp_path):
    """LR-R1-M1 ``candidate.change`` at the binding: per staged path, the original raw bytes (``all_original_raw``,
    None = created) against the worktree bytes, in path order."""
    workspace, worktree = _trees(tmp_path, {"calc.py": CALC}, {"calc.py": CALC_NEW, "new.py": "N = 1\n"})
    state = _state("calc.py", "new.py")
    state.candidate_digests = {"calc.py": raw_digest(CALC_NEW.encode()), "new.py": raw_digest(b"N = 1\n")}
    state.all_original_raw = {"calc.py": b"def add(a, b):\n    return a - b\n"}   # differs from the worktree's base
    ctx = _ctx(workspace, worktree, _config())
    emitted = []
    with patch("kriya.core.attempt_evidence.scope.capture_mode", return_value="full"), \
            patch("kriya.core.attempt_evidence.scope.emit",
                  side_effect=lambda kind, payload, content=None: emitted.append((kind, payload, content))):
        attempt._bind_verification_tree(state, ctx)
    assert [(kind, payload["path"]) for kind, payload, _c in emitted] == [("candidate.change", "calc.py"),
                                                                          ("candidate.change", "new.py")]
    calc, new = emitted[0], emitted[1]
    assert calc[1]["before_digest"] == raw_digest(b"def add(a, b):\n    return a - b\n")
    assert calc[1]["after_digest"] == raw_digest(CALC_NEW.encode()) and calc[1]["created"] is False
    assert calc[2]["before"] == b"def add(a, b):\n    return a - b\n" and calc[2]["after"] == CALC_NEW.encode()
    assert "-    return a - b" in calc[2]["diff"] and "+    return a + b" in calc[2]["diff"]
    assert new[1]["created"] is True and new[1]["before_digest"] is None and new[2]["before"] is None


# ----------------------------------------------------------------------------- the candidate validator

def test_the_candidate_validator_is_built_from_the_attempts_trees_policy_and_resolved_migration(tmp_path):
    from kriya.workflow.migration import MigrationResolutionStatus

    class _Obligation:
        source_artifacts = ("com.google.code.gson:gson",)

    class _Resolution:
        def __init__(self, status):
            self.status = status
            self.obligation = _Obligation()
    workspace, worktree = _trees(tmp_path, {"calc.py": CALC}, {"calc.py": CALC_NEW})
    cfg = _config()
    script = _Script()
    script.autonomy = cfg.autonomy
    ctx = _ctx(workspace, worktree, cfg, migration_resolution=_Resolution(MigrationResolutionStatus.RESOLVED),
               write_scope_mode=WriteScopeMode.ALLOWLIST, allowed_write_relpaths=["pom.xml"])
    with patch("kriya.tools.validate.PolymorphicValidator", script.validator_class()):
        attempt._new_candidate_validator(ctx)
        ctx.migration_resolution = None
        attempt._new_candidate_validator(ctx)
        ctx.migration_resolution = _Resolution(MigrationResolutionStatus.INDETERMINATE)   # identity not resolved
        attempt._new_candidate_validator(ctx)
    first, second, third = script.log
    assert first == {"call": "construct", "validator": 0, "workspace_path": worktree, "original_workspace_path": workspace,
                     "autonomy_cfg": "the kernel's", "java_home_override": None,
                     "authorized_dependency_removals": ["com.google.code.gson:gson"],
                     "toolchain_declaration_mutable": attempt._ctx_toolchain_declaration_mutable(ctx)}
    assert second["authorized_dependency_removals"] == [] and third["authorized_dependency_removals"] == []


# ----------------------------------------------------------------------------- the verification-only attempt

def test_the_verification_only_attempt(tmp_path):
    """A DENY_ALL unit with declared verifiers: one attempt number, the LR-R1-P4 inputs digest, a validator over the
    worktree, the declared verifiers through VerificationCoordinator (compile over the ESTABLISHED files only, the
    suite judged with the verification-unit scope), then candidate_gates.passed (verification_only)."""
    async def case(state, ctx, script):
        await attempt._run_verification_only_attempt(state, ctx)
    script = _Script()
    state = _state("calc.py", "written_only.py", attempt_number=3)   # a written file outside the established ones
    form = run_gates(tmp_path, case, script=script, state=state, ctx_overrides=dict(
        write_scope_mode=WriteScopeMode.DENY_ALL, established_files=["lib.py", "calc.py"],
        required_verification=[{"type": "tool", "tool_name": "quality_gates", "description": "gates"}]))
    assert state.attempt_number == 4 and state.verification_only_inputs_attempt == 4
    form["inputs_digest_present"] = bool(state.verification_only_inputs)
    _compare("attempt_verification_only", form)


def test_the_verification_only_attempt_failure(tmp_path):
    async def case(state, ctx, script):
        await attempt._run_verification_only_attempt(state, ctx)
    script = _Script(tests=[FAIL_TESTS])
    form = run_gates(tmp_path, case, script=script, ctx_overrides=dict(
        write_scope_mode=WriteScopeMode.DENY_ALL,
        required_verification=[{"type": "tool", "tool_name": "test", "description": "tests"}]))
    _compare("attempt_verification_only_failure", form)


# ----------------------------------------------------------------------------- post-move pin (E8 mutant S09)

def test_the_gates_log_through_the_attempt_modules_logger(tmp_path, caplog):
    """The candidate gates' log records keep the logger they always had (``kriya.workflow.attempt``: log filters and
    the run log key on it), wherever the gate bodies live. Post-move mutant S09 (the deps builder handing the moved
    gates another logger) survived every tier before this pin; green at 74f01ac too."""
    import logging

    async def case(state, ctx, script):
        await _executing(state, ctx, script)
    with caplog.at_level(logging.DEBUG):
        run_gates(tmp_path, case, script=_Script(stack="java"), files={"pom.xml": POM, APP: APP_BASE},
                  candidate={APP: APP_GOOD}, state=_state(APP), cfg=_config(),
                  ctx_overrides=dict(goal=JVM_GOAL, established_files=[]))
    gate_records = [r for r in caplog.records if r.getMessage().startswith(("Quality Gates:", "Toolchain preflight"))]
    assert gate_records and {r.name for r in gate_records} == {"kriya.workflow.attempt"}
