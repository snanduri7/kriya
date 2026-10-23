"""E14 preflight characterization (full modularization W4, owner decision 2026-10-19 item 8): TODAY's behaviour of
OI-05, OI-06, OI-40, OI-42, OI-59, OI-63 and OI-65, each pinned through the real path the item names, with NO production
change. Every test name says ``current_behaviour`` and names its OI; every docstring says whether the pinned behaviour
is suspected defective. A later slice that corrects one of them changes the matching test deliberately (and says so).
The classification, owner and closing-slice evidence is in handover/MODULARIZATION/E14-CHARACTERIZATION.md; the mutation
that kills each pin is recorded there.

Paths driven (only the model scripted unless stated):
- OI-42: the legacy pipeline in human-in-the-loop mode (``_scripted_run``: real gates, real pytest, real git sandbox,
  real terminal commit), a pass-through spy on ``PolymorphicValidator.run_tests`` that records which bytes each test
  run executed and whether it was the terminal regression (by call stack, never by patching a kernel name);
- OI-05 / OI-06 / OI-40: the kernel golden scenarios (tests/state_machine/_kernel_golden.py) on every terminal path
  class, read back from the result, the RunRecords, the evidence stores, the checkpoints and the traces.db rows;
- OI-59: ``WorkflowController.execute`` (legacy) on a workspace whose target file is git-ignored;
- OI-63: ``WorkflowController(real WorkflowEngine).execute_milestones`` (the tests/test_e11_milestones_real_engine.py
  approach, re-built here so this file depends on no other test module: OI-34);
- OI-65: ``WorkflowController.execute(migration_mode="enforce")`` with the REAL structured-plan parse, plan build and
  ``validate_plan`` (a strict engine over a real AppConfig: tests/_strict_doubles.py); only the Planner text scripted.
No kernel module name is patched (tests/architecture/test_patch_points.py counts are unchanged); the file is not in
the kernel / state_machine tiers (it drives ~40 real scenario runs).
"""
import asyncio
import glob
import hashlib
import inspect
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position
from _scripted_run import engine, git_workspace, script  # noqa: E402  # pylint: disable=wrong-import-position
from _strict_doubles import strict_config, strict_engine  # noqa: E402  # pylint: disable=wrong-import-position

from kriya.config.config import AppConfig  # noqa: E402
from kriya.control.decisions import load_decision_ledger  # noqa: E402
from kriya.control.persistence import list_run_records  # noqa: E402
from kriya.core import LLMClient  # noqa: E402
from kriya.core.attempt_evidence import reader  # noqa: E402
from kriya.core.kernel import Kernel  # noqa: E402
from kriya.core.state_paths import ENV_STATE_DIR, trace_db_path  # noqa: E402
from kriya.tools.validate import PolymorphicValidator  # noqa: E402
from kriya.workflow.milestones import load_or_resume_milestone_run_state  # noqa: E402
from kriya.workflow.obligations import ObligationLedger  # noqa: E402
from kriya.workflow.plan_schema import (  # noqa: E402
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
)
from kriya.workflow.plan_validation import validate_plan  # noqa: E402
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass  # noqa: E402
from kriya.workflow.workflow import WorkflowEngine  # noqa: E402
from kriya.workflow.workflow_controller import WorkflowController  # noqa: E402
from kriya.workflow.workflow_types import WorkflowResult  # noqa: E402


@pytest.fixture(autouse=True)
def _fixed_git_identity(monkeypatch):
    for name, value in kg.GIT_ENV.items():
        monkeypatch.setenv(name, value)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sandboxes(workspace: str) -> List[str]:
    """The run's git worktrees other than the workspace itself (the candidate sandbox)."""
    out = subprocess.run(["git", "-C", workspace, "worktree", "list", "--porcelain"], capture_output=True, text=True,
                         check=True).stdout
    return [line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("worktree ")][1:]


def _read(root: str, name: str = "calc.py") -> str:
    with open(os.path.join(root, name), encoding="utf-8") as handle:
        return handle.read()


def _trace_rows(cfg) -> List[tuple]:
    path = trace_db_path(cfg)
    if not os.path.isfile(path):
        return []
    conn = sqlite3.connect(path)
    try:
        return [tuple(row) for row in conn.execute(
            "SELECT run_id, status, failure_category, run_events FROM runs ORDER BY timestamp, rowid")]
    finally:
        conn.close()


# ===================================================================================================== OI-42

class _Hitl:
    """One legacy run (human-in-the-loop by default) with an approver, observed at the three byte boundaries: what the
    person approved (the sandbox bytes when the callback is asked), what each test run executed (and whether it was the
    terminal full regression), and every Reviewer request (its system prompt, the candidate it was shown) with a reply
    that names the call number, so the final review text says which Reviewer call produced it."""

    TAMPERED = kg.GOOD + "\n# changed after the approval\n"

    def __init__(self, tmp_path: Path, developer, *, tamper_in_callback: bool = False,
                 tamper_in_terminal_regression: bool = False, configure=None):
        self.tmp_path = tmp_path
        self.workspace = git_workspace(tmp_path, {"calc.py": kg.BASE, "test_calc.py": kg.TESTS})
        self.developer = developer
        self.tamper_in_callback, self.tamper_in_terminal_regression = tamper_in_callback, tamper_in_terminal_regression
        self.configure = configure
        self.approvals: List[str] = []         # sha256 of the sandbox calc.py the person was asked to approve
        self.test_runs: List[Dict[str, Any]] = []
        self.reviews: List[Dict[str, Any]] = []
        self.result: Any = None
        self.cfg: Any = None
        self.engine: Any = None

    def _approval(self, diffs, reason):
        (sandbox,) = _sandboxes(self.workspace)
        self.approvals.append(_sha(_read(sandbox)))
        if self.tamper_in_callback:
            with open(os.path.join(sandbox, "calc.py"), "w", encoding="utf-8") as handle:
                handle.write(self.TAMPERED)
        return True

    def run(self) -> "_Hitl":
        real_run_tests = PolymorphicValidator.run_tests
        hitl = self

        def observed_run_tests(validator, target_test=None, **kwargs):
            outcome = real_run_tests(validator, target_test, **kwargs)
            terminal = any(frame.function == "run_terminal_regression" for frame in inspect.stack())
            if terminal and hitl.tamper_in_terminal_regression and validator.workspace_path != hitl.workspace:
                with open(os.path.join(validator.workspace_path, "calc.py"), "w", encoding="utf-8") as handle:
                    handle.write(hitl.TAMPERED)
            hitl.test_runs.append({"terminal": terminal, "executed": _sha(_read(validator.workspace_path)),
                                   "passed": bool(outcome.get("success"))})
            return outcome

        self.engine, llm, self.cfg = engine(chain=False, configure=self.configure)
        script(llm, plan="Step 1: add mul", target="calc.py", developer=self.developer)
        scripted = llm.complete

        async def complete(system_prompt, user_prompt, *args, **kwargs):
            if (system_prompt or "").startswith(kg.REVIEWER):
                shown = [n for n in range(1, 12) if kg._oscillating(n) in user_prompt] + (
                    ["GOOD"] if kg.GOOD in user_prompt else [])
                self.reviews.append({"system": _sha(system_prompt), "shown": shown})
                return f"REVIEW-CALL-{len(self.reviews)}"
            return await scripted(system_prompt, user_prompt, *args, **kwargs)
        llm.complete = complete
        with kg.isolated_working_directory(self.tmp_path), patch.object(PolymorphicValidator, "run_tests",
                                                                        observed_run_tests):
            self.result = asyncio.run(self.engine.run_generation_workflow(
                goal=kg.GOAL, workspace_path=self.workspace, approval_callback=self._approval))
        return self

    def standard_reviewer_prompt(self) -> str:
        return _sha(self.engine.reviewer.system_prompt)

    def approval_events(self) -> List[Dict[str, Any]]:
        (row,) = _trace_rows(self.cfg)
        return [event for event in json.loads(row[3] or "[]") if event["kind"] == "approval.decision"]


def test_oi42_current_behaviour_final_review_reuses_the_pre_build_review_after_a_terminal_regression_failure(tmp_path):
    """OI-42, SUSPECTED DEFECTIVE (reporting, not authority; E14-CHARACTERIZATION.md class (ii)). Human-in-the-loop,
    every attempt's candidate passes the held gates and fails the terminal full regression (the real pytest gate) after
    the person approved it. Pinned with digests: each approval covered exactly the bytes the terminal regression then
    executed; the run applies nothing; the Reviewer is called once per attempt, always at the approval gate (the
    pre-build review) with the standard system prompt; the final ``review`` is the LAST pre-approval review
    (``REVIEW-CALL-8``, the review of the bytes approved last) and ``review_included_in_approval`` is True, so no
    rejected-candidate diagnostic is produced (the CLI then prints no review: ``kriya/cli.py``, review shown only when
    ``review_included_in_approval`` is false)."""
    run = _Hitl(tmp_path, lambda model, n, text: kg._oscillating(n)).run()
    legacy = run.result
    candidates = [_sha(kg._oscillating(n)) for n in range(1, 9)]
    assert run.approvals == candidates                                       # one approval per attempt, its own bytes
    assert [r["executed"] for r in run.test_runs] == candidates              # executed == approved, attempt by attempt
    assert [(r["terminal"], r["passed"]) for r in run.test_runs] == [(True, False)] * 8
    assert [r["shown"] for r in run.reviews] == [[n] for n in range(1, 9)]   # each review saw its attempt's candidate
    assert {r["system"] for r in run.reviews} == {run.standard_reviewer_prompt()}   # never the rejected-candidate prompt
    assert (legacy["review"], legacy["review_included_in_approval"]) == ("REVIEW-CALL-8", True)
    assert (legacy["quality_gates_passed"], legacy["failure_category"]) == (False, "quality_gates_exhausted")
    assert (legacy["candidate_gates_passed"], legacy["terminal_regression_passed"]) == (True, False)
    assert _read(run.workspace) == kg.BASE                                   # nothing applied


def test_oi42_current_behaviour_the_autonomous_twin_produces_the_rejected_candidate_diagnostic(tmp_path):
    """OI-42 control (NOT defective): the same trajectory with no approval gate (autonomous mode) reaches the
    rejected-candidate diagnostic review: one Reviewer call, after the loop, with its own (non-standard) system prompt
    and the last failing candidate; ``review_included_in_approval`` is False. Discriminates the reuse above from 'no
    review at all'."""
    def autonomous(cfg):
        cfg.autonomy.mode = "guardrails"
    run = _Hitl(tmp_path, lambda model, n, text: kg._oscillating(n), configure=autonomous).run()
    attempts = len(run.test_runs)
    assert run.approvals == [] and attempts >= 2
    assert [r["shown"] for r in run.reviews] == [[attempts]]
    assert run.reviews[0]["system"] != run.standard_reviewer_prompt()
    assert run.result["review_included_in_approval"] is False
    # The diagnostic filter withholds a reply without the DIAGNOSTIC FINDINGS markers (this scripted one has none).
    assert run.result["review"].startswith(
        "[Reviewer output did not follow the required rejected-candidate report structure")


@pytest.mark.parametrize("window", ["approval_callback", "terminal_regression"])
def test_oi42_current_behaviour_an_approval_never_covers_bytes_changed_after_it(tmp_path, window):
    """OI-42 authority boundary, NOT defective: bytes changed after the person approved are never applied. A change
    inside the approval callback is refused by the source-review guard before anything runs; a change during the
    terminal regression (after the guard's last check) is refused by the terminal commit binding. Both stop with
    nothing applied."""
    run = _Hitl(tmp_path, lambda model, n, text: kg.GOOD, tamper_in_callback=window == "approval_callback",
                tamper_in_terminal_regression=window == "terminal_regression").run()
    legacy = run.result
    assert run.approvals == [_sha(kg.GOOD)]
    if window == "approval_callback":
        assert run.test_runs == []    # the guard refuses before any execution
        assert legacy["environment_failure"].startswith("CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW:")
        assert legacy["failure_category"] == "environment_failure"
    else:
        assert run.test_runs == [{"terminal": True, "executed": _sha(run.TAMPERED), "passed": True}]
        assert legacy["environment_failure"].startswith(
            "WORKSPACE_COMMIT_NOT_COMPLETED: VERIFIED_CANDIDATE_EVIDENCE_STALE (workspace UNCHANGED)")
        assert legacy["failure_category"] == "workspace_commit_failed"
    assert legacy["quality_gates_passed"] is False
    assert _read(run.workspace) == kg.BASE


def test_oi42_current_behaviour_the_durable_approval_record_names_no_candidate_digest(tmp_path):
    """OI-42 evidence binding, SUSPECTED DEFECTIVE (OI-68): the approval is bound to the candidate only in memory (the
    held-gate guard and the terminal commit compare digests, pinned above); the durable ``approval.decision`` event
    the trace and the attempt-evidence mirror keep carries only the outcome and the triggers, so the record cannot
    say which bytes the person approved."""
    run = _Hitl(tmp_path, lambda model, n, text: kg.GOOD).run()
    assert run.result["quality_gates_passed"] is True and _read(run.workspace) == kg.GOOD
    (event,) = run.approval_events()
    assert event["details"] == {"outcome": "approved", "triggers": ["human_in_the_loop"]}
    assert event["authority"] == "authoritative"


def test_oi42_current_behaviour_a_resumed_run_asks_for_approval_again(tmp_path):
    """OI-42, NOT defective: a run whose last candidate passed the held gates and failed the terminal regression leaves
    a ``candidate_gates_passed`` checkpoint; resuming it regenerates the candidate and asks the person again (no
    approval is carried across runs)."""
    workspace = git_workspace(tmp_path, {"calc.py": kg.BASE, "test_calc.py": kg.TESTS})
    seen = []
    for developer, resume in ((lambda m, n, t: kg._oscillating(n), False), (lambda m, n, t: kg.GOOD, True)):
        eng, llm, _cfg = engine(chain=False)
        calls: List[str] = []
        kg._role_script(llm, developer, calls)
        approvals: List[int] = []
        with kg.isolated_working_directory(tmp_path):
            result = asyncio.run(WorkflowController(eng).execute(
                goal=kg.GOAL, workspace_path=workspace, migration_mode="legacy", resume=resume,
                approval_callback=lambda diffs, reason, approvals=approvals: approvals.append(1) or True))
        checkpoints = [json.loads(Path(p).read_text())
                       for p in glob.glob(os.path.join(workspace, ".kriya", "checkpoints", "*.json"))]
        seen.append((len(calls), len(approvals), result.legacy_result["quality_gates_passed"],
                     [(c["stage"], c["candidate_gates_passed"]) for c in checkpoints]))
    assert seen == [(8, 8, False, [("candidate_gates_passed", True)]), (1, 1, True, [("candidate_gates_passed", True)])]
    assert _read(workspace) == kg.GOOD


# ===================================================================================== OI-05 / OI-06 / OI-40

# One scenario per terminal-path class (the golden scenarios; their goldens pin the full forms). Expected per path:
# (the result dict's run_id kind, its failure_category (MISSING = key absent), its quality_gates_passed, its status,
# the settled RunRecord's retry_state_reference ("result" = "checkpoint:<the result dict's run_id>"), the
# WorkflowResult.run_id kind, the traces.db rows as (status, failure_category)).
MISSING = "<absent>"
PATH_TABLE: Dict[str, tuple] = {
    # success
    "direct_success": ("engine", None, True, MISSING, "result", None, [("success", None)]),
    "legacy_success": ("engine", None, True, MISSING, "result", "run_record", [("success", None)]),
    "legacy_approval_diff_size_trigger": ("engine", None, True, MISSING, "result", "run_record", [("success", None)]),
    "enforce_success_with_verification_unit": ("run_record", MISSING, True, "success", None, "run_record",
                                               [("success", None)] * 3),
    # stopped early / gate failed (stage 4 and the terminal apply)
    "legacy_gates_exhausted": ("checkpoint", "quality_gates_exhausted", False, MISSING, None, "run_record",
                               [("failure", "quality_gates_exhausted")]),
    "legacy_no_progress": ("checkpoint", "no_progress", False, MISSING, None, "run_record", [("failure", "no_progress")]),
    "legacy_budget_stop": ("checkpoint", "generation_budget_exhausted", False, MISSING, None, "run_record",
                           [("failure", "generation_budget_exhausted")]),
    "legacy_static_analysis_blocked": ("checkpoint", "static_analysis_blocked", False, MISSING, None, "run_record",
                                       [("failure", "static_analysis_blocked")]),
    "legacy_commit_revision_conflict": ("checkpoint", "workspace_commit_failed", False, MISSING, "result", "run_record",
                                        [("failure", "workspace_commit_failed")]),
    "legacy_final_review_refused": ("engine", "final_review_refused", False, MISSING, "result", "run_record",
                                    [("failure", "final_review_refused")]),
    "enforce_verification_only_failed": ("run_record", MISSING, False, "failed", None, "run_record",
                                         [("success", None), ("failure", "no_progress"), ("failure", "no_progress"),
                                          ("failed", "failed")]),
    # approval aborts (OI-06): the trace row carries the category, the dict does not
    "legacy_approval_refused": ("engine", MISSING, False, MISSING, None, "run_record",
                                [("human_rejected", "human_rejected")]),
    "legacy_approval_required_no_callback": ("engine", MISSING, False, MISSING, None, "run_record",
                                             [("approval_required", "approval_required")]),
    "enforce_approval_refused": ("run_record", MISSING, False, "failed", None, "run_record",
                                 [("human_rejected", "human_rejected"), ("failed", "failed")]),
    # design-stage stops (OI-40) and the stage-4 prelude
    "legacy_knowledge_gap": ("trace_row", MISSING, MISSING, "knowledge_gap", None, "run_record",
                             [("knowledge_gap", "knowledge_gap")]),
    "legacy_planner_output_incomplete": ("trace_row", MISSING, MISSING, "planner_output_incomplete", None, "run_record",
                                         [("planner_output_incomplete", "planner_output_incomplete")]),
    "legacy_planner_unauthorized_path": ("trace_row", MISSING, MISSING, "planner_output_unauthorized_path", None,
                                         "run_record",
                                         [("planner_output_unauthorized_path", "planner_output_unauthorized_path")]),
    "legacy_planner_schema_invalid": ("trace_row", MISSING, MISSING, "planner_output_schema_invalid", None, "run_record",
                                      [("planner_output_schema_invalid", "planner_output_schema_invalid")]),
    "legacy_architect_file_list_rejected": ("trace_row", MISSING, MISSING, "architect_file_list_rejected", None,
                                            "run_record",
                                            [("architect_file_list_rejected", "architect_file_list_rejected")]),
    "legacy_baseline_indeterminate": ("trace_row", MISSING, False, "baseline_indeterminate", None, "run_record",
                                      [("baseline_indeterminate", "baseline_indeterminate")]),
    "legacy_worktree_sync_refused": ("trace_row", "worktree_sync_failed", False, "failure", None, "run_record",
                                     [("failure", "worktree_sync_failed")]),
    # plan invalid / workspace refusals
    "enforce_plan_invalid": ("run_record", MISSING, False, "needs_review", None, "run_record",
                             [("needs_review", "PLANNING_ERROR")]),
    "legacy_workspace_refused": ("workflow_result_only", MISSING, False, "needs_review", MISSING, "no_run_record", []),
    "direct_workspace_refused": (None, MISSING, False, "needs_review", MISSING, None, []),
}


def _path_row(outcome: Any) -> tuple:
    result = outcome.result
    wr_id = result.run_id if isinstance(result, WorkflowResult) else None
    legacy = (result.legacy_result if isinstance(result, WorkflowResult) else result) or {}
    records = list_run_records(outcome.workspace)
    record_ids = {r.run_id for r in records}
    checkpoints = {os.path.basename(p)[:-len(".json")]
                   for p in glob.glob(os.path.join(outcome.workspace, ".kriya", "checkpoints", "*.json"))}
    rows = _trace_rows(outcome.cfg) if outcome.cfg is not None else []
    legacy_id = legacy.get("run_id")
    settled = [r.retry_state_reference for r in records if r.terminal_status is not None]
    if legacy_id is None:
        kind = None
    elif legacy_id in record_ids:
        kind = "run_record"
    elif legacy_id in {row[0] for row in rows}:
        kind = "trace_row"
    elif legacy_id in checkpoints:
        kind = "checkpoint"
    elif legacy_id == wr_id:
        kind = "workflow_result_only"
    else:
        kind = "engine"   # the engine's own run id (its checkpoint id; the checkpoint was deleted)
    if not settled:
        reference = MISSING
    elif settled[-1] is not None and legacy_id is not None and settled[-1] == f"checkpoint:{legacy_id}":
        reference = "result"
    else:
        reference = settled[-1]
    wr_kind = None if wr_id is None else ("run_record" if wr_id in record_ids else "no_run_record")
    # Every evidence store is a RunRecord's (id for id), and each surviving checkpoint names the RunRecord it belongs to.
    evidence = set(reader.list_runs(os.environ[ENV_STATE_DIR]))
    assert evidence <= record_ids, (evidence, record_ids)
    for name in checkpoints:
        link = json.loads(Path(outcome.workspace, ".kriya", "checkpoints", f"{name}.json").read_text()).get(
            "_run_record")
        assert link is None or link["run_id"] in record_ids, link
    return (kind, legacy.get("failure_category", MISSING), legacy.get("quality_gates_passed", MISSING),
            legacy.get("status", MISSING), reference, wr_kind, [(row[1], row[2]) for row in rows])


@pytest.mark.parametrize("name", sorted(PATH_TABLE))
def test_oi05_oi06_oi40_current_behaviour_result_identity_and_category_on_every_terminal_path(name, tmp_path):
    """OI-05 (NOT defective for any traced consumer; an ambiguous contract), OI-06 / OI-40 (SUSPECTED DEFECTIVE: result
    contract inconsistency; one consumer, control/launch.py ``finish``, copies the dict's failure_category into the
    desktop launch record). The result dict's ``run_id`` is a different identity per path: the engine's checkpoint id
    on the stage-4 paths (the CLI resume hint's input), the traces.db row id on the design-stage and prelude stops (the
    knowledge-gap retry's ``trace_id_override``), the RunRecord id on the enforce paths; the WorkflowResult.run_id is
    the RunRecord's (its evidence store has the same id) and the RunRecord names the engine id as
    ``retry_state_reference`` only once the terminal apply ran. ``failure_category`` is absent from the dict on the
    approval aborts, the design-stage stops, the stage-4 prelude baseline stop, the PRD-008 refusals and every enforce
    path, while the trace row carries it (= the status)."""
    assert _path_row(kg.SCENARIOS[name](tmp_path)) == PATH_TABLE[name]


# ===================================================================================================== OI-59

def test_oi59_current_behaviour_a_git_ignored_target_stops_before_the_staging_loop(tmp_path):
    """OI-59, the route the item names (a workspace file the sandbox sync does not carry: a git-ignored target) through
    the real kernel entry. NOT the suspected staging-loop path: the Developer is never called; the edit-capability
    decision (kriya/workflow/candidate_transaction.py ``_decide_edit_capabilities``) has no authoritative source for a
    file the sandbox lacks (the producers of full-source authority read the sandbox) and stops with the typed
    ``context_edit_protocol_unsatisfiable`` refusal; nothing is written. The staging loop's workspace-base fallback
    (``_stage_candidate_writes``, untyped ``BatchCommitError``) stays pinned at unit level only
    (tests/test_candidate_transaction.py); its reach through a real run is UNKNOWN (E14-CHARACTERIZATION.md)."""
    workspace = git_workspace(tmp_path, {"calc.py": kg.BASE, "test_calc.py": kg.TESTS, ".gitignore": "calc.py\n"})
    tracked = subprocess.run(["git", "ls-files"], cwd=workspace, capture_output=True, text=True, check=True).stdout
    assert tracked.split() == [".gitignore", "test_calc.py"]
    eng, llm, cfg = engine(chain=False)
    record = script(llm, plan="Step 1: add mul", target="calc.py", developer=lambda m, n, t: kg.GOOD)
    with kg.isolated_working_directory(tmp_path):
        result = asyncio.run(WorkflowController(eng).execute(
            goal=kg.GOAL, workspace_path=workspace, approval_callback=lambda diffs, reason: True,
            migration_mode="legacy"))
    legacy = result.legacy_result
    assert record.developer_calls == []
    assert (legacy["quality_gates_passed"], legacy["failure_category"]) == (False, "context_edit_protocol_unsatisfiable")
    assert legacy["environment_failure"].startswith(
        "CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE: no mutation operation is feasible under the authoritative context of this "
        "Developer invocation - calc.py: no authoritative full source")
    assert legacy["last_failure"]["type"] == "context_edit_protocol_unsatisfiable"
    assert [(row[1], row[2]) for row in _trace_rows(cfg)] == [("failure", "context_edit_protocol_unsatisfiable")]
    assert _read(workspace) == kg.BASE


# ===================================================================================================== OI-63

_GROUP = "grp-e14"
_CALC = "def add(a, b):\n    return a + b\n"
_WITH_MUL = _CALC + "\n\ndef mul(a, b):\n    return a * b\n"
_WITH_DIV = _WITH_MUL + "\n\ndef div(a, b):\n    return a / b\n"


def _milestone_plan() -> Dict[str, Any]:
    return {"group_id": _GROUP, "original_goal": "Add mul and div to calc.py.", "milestones": [
        {"id": "M1", "goal": "Add mul(a, b) to calc.py.", "success_criterion": "mul works", "depends_on": []},
        {"id": "M2", "goal": "Add div(a, b) to calc.py.", "success_criterion": "div works", "depends_on": ["M1"]},
    ]}


def _milestone_run(tmp_path: Path, workspace: Path, answers: Dict[int, str]):
    """One ``kriya generate --from-milestones`` through ``WorkflowController(real WorkflowEngine).execute_milestones``
    with a role-keyed scripted model (answers: milestone index -> calc.py; 3 = the integration unit; a missing index
    answers no file, so that unit fails its gates)."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = []
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.skills = str(tmp_path / "skills")
    units: List[Optional[int]] = []

    async def complete(system_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "Developer Agent" in first:
            content = answers.get(units[-1])
            return "[]" if content is None else json.dumps([{"filepath": "calc.py", "content": content}])
        if "File List Planner" in first:
            return '["calc.py"]'
        if "Planner Agent" in first:
            return "Step 1: edit calc.py"
        if "Architect Agent" in first:
            return "Design: edit calc.py"
        return "Review: Approved"

    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=complete)
    eng = WorkflowEngine(Kernel(config=cfg), llm)
    eng.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    real = eng.run_generation_workflow

    async def spy(*args, **kwargs):
        units.append(kwargs.get("milestone_index"))
        return await real(*args, **kwargs)
    eng.run_generation_workflow = spy
    state = load_or_resume_milestone_run_state(str(workspace), _milestone_plan())
    result = asyncio.run(WorkflowController(eng).execute_milestones(state, str(workspace)))
    return result, units


def _strip_ownership(path: Path) -> None:
    payload = json.loads(path.read_text())
    assert payload.pop("_workspace", None) is not None, path
    path.write_text(json.dumps(payload))


def test_oi63_is_moot_a_resume_over_ownerless_control_documents_is_refused_and_nothing_is_adopted(tmp_path):
    """OI-63 (the migration decision omitted the sidecar it never saw ownerless) concerned the implicit ownership migration that D2 M4 (owner
    decision 2026-10-23) RETIRED: a resume over ownerless (pre-ownership) control documents through the real ``execute_milestones`` shape is now a
    typed refusal (``WORKSPACE_OWNERSHIP_MISSING``) before any model work, and every document keeps the bytes it had (nothing is stamped)."""
    from kriya.context.workspace_identity import WorkspaceOwnershipMissing

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "calc.py").write_text(_CALC)
    for argv in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "T"],
                 ["add", "calc.py"], ["commit", "-qm", "calc"]):
        subprocess.run(["git", *argv], cwd=workspace, check=True)
    first, _units = _milestone_run(tmp_path, workspace, {1: _WITH_MUL})   # M1 completes, M2 fails its gates
    assert first.legacy_result["status"] == "milestone_failed"
    kriya_dir = workspace / ".kriya"
    sidecar = kriya_dir / "milestones" / f"{_GROUP}.json"
    stripped = (kriya_dir / "control" / "state.json", kriya_dir / "control" / "contracts.json", sidecar,
                *sorted((kriya_dir / "checkpoints").glob("*.json")))
    for path in stripped:
        _strip_ownership(path)
    before = {path: path.read_bytes() for path in stripped}
    with pytest.raises(WorkspaceOwnershipMissing) as refused:
        _milestone_run(tmp_path, workspace, {2: _WITH_DIV, 3: _WITH_DIV})
    assert refused.value.reason_code == "WORKSPACE_OWNERSHIP_MISSING"
    assert {path: path.read_bytes() for path in stripped} == before
    assert not [d for d in load_decision_ledger(str(workspace)).all() if d.type == "legacy_workspace_ownership_migrated"]


# ===================================================================================================== OI-65

_ROUTE = EngineeringRoute(kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
                          current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
                          execution_weight=ExecutionWeight.LIGHT)
_COMPILE = VerificationMethod(type=VerificationMethodType.TOOL, description="compiles", tool_name="compile")


def _unit(sid: str, path: str, **fields: Any) -> Subtask:
    return Subtask(id=sid, description=f"write {path}", execution_method=ExecutionMethod.MODEL,
                   planned_files=[PlannedFile(path=path, action=FileAction.CREATE)], verification=[_COMPILE], **fields)


# The second requirer of `cap` reuses the id s2; whether its depends_on names the provider decides SATISFIED/VIOLATED.
_PLANS = {
    "duplicate_id_satisfied_then_violated": [
        _unit("s1", "a.py", provides=["cap"]), _unit("s2", "b.py", requires=["cap"], depends_on=["s1"]),
        _unit("s2", "c.py", requires=["cap"])],
    "duplicate_id_violated_then_satisfied": [
        _unit("s1", "a.py", provides=["cap"]), _unit("s2", "c.py", requires=["cap"]),
        _unit("s2", "b.py", requires=["cap"], depends_on=["s1"])],
    "duplicate_id_both_satisfied": [
        _unit("s1", "a.py", provides=["cap"]), _unit("s2", "b.py", requires=["cap"], depends_on=["s1"]),
        _unit("s2", "c.py", requires=["cap"], depends_on=["s1"])],
    "unique_ids": [_unit("s1", "a.py", provides=["cap"]), _unit("s2", "b.py", requires=["cap"], depends_on=["s1"])],
}


def _planner_text(name: str) -> str:
    return json.dumps({"subtasks": [unit.model_dump(mode="json") for unit in _PLANS[name]]})


def _enforce_engine(tmp_path: Path, planner_text: str):
    cfg = strict_config(autonomy={"spec_compliance_enabled": False}, paths={"memory": str(tmp_path / "memory")})
    we = strict_engine(cfg)
    we.engineering_triage.classify = AsyncMock(return_value=_ROUTE)
    we.engineering_triage.recompute_from_files = AsyncMock(return_value=_ROUTE)
    we.planner.run = AsyncMock(return_value=planner_text)
    we.planner.max_output_tokens = None
    we.run_generation_workflow = AsyncMock(side_effect=AssertionError("planning never converges here"))
    return we, cfg


@pytest.mark.asyncio
async def test_oi65_current_behaviour_a_same_round_regression_in_round_0_raises_typeerror(tmp_path):
    """OI-65, CONFIRMED DEFECTIVE (reachable): a Planner response that repeats a subtask id, the first copy declaring
    the provider in depends_on and the second not, makes the REAL ``validate_plan`` record the same
    SUBTASK_SEMANTIC_CONTRACT obligation SATISFIED then VIOLATED within round 0 (a regression event), so the enforce
    PLAN_REPAIR loop treats round 0 as a regression and seeds the repair prompt from the retained baseline, which round
    0 does not have: ``list(None)`` raises TypeError out of ``WorkflowController.execute``. One Planner call, no
    generation, the RunRecord settles FAILURE and the trace row is ``error`` / ``TypeError``."""
    we, cfg = _enforce_engine(tmp_path, _planner_text("duplicate_id_satisfied_then_violated"))
    with pytest.raises(TypeError, match="'NoneType' object is not iterable"):
        await WorkflowController(we).execute("Create a.py", str(tmp_path), migration_mode="enforce")
    assert we.planner.run.await_count == 1 and we.run_generation_workflow.await_count == 0
    assert [(r.lifecycle_state.value, r.terminal_status) for r in list_run_records(str(tmp_path))] == [
        ("FAILURE", "FAILURE")]
    assert [(row[0].endswith(".enforce"), row[1], row[2]) for row in _trace_rows(cfg)] == [(True, "error", "TypeError")]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["duplicate_id_violated_then_satisfied", "duplicate_id_both_satisfied", "unique_ids"])
async def test_oi65_current_behaviour_the_controls_end_as_a_typed_plan_repair_outcome(tmp_path, name):
    """OI-65 negative controls (NOT defective): the same duplicate id with VIOLATED first (no SATISFIED -> VIOLATED
    downgrade), both copies satisfied, or unique ids: no round-0 regression, the bounded PLAN_REPAIR loop runs its three
    Planner rounds and ends typed (needs_review, STRUCTURED_PLAN_REPAIR_EXHAUSTED)."""
    we, _cfg = _enforce_engine(tmp_path, _planner_text(name))
    result = await WorkflowController(we).execute("Create a.py", str(tmp_path), migration_mode="enforce")
    legacy = result.legacy_result
    assert legacy["status"] == "needs_review" and legacy["plan_repair_attempts"] == 2
    assert "STRUCTURED_PLAN_REPAIR_EXHAUSTED" in legacy["reason_codes"]
    assert we.planner.run.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("name,regressions", [
    ("duplicate_id_satisfied_then_violated", ["plan.subtask.s2.requires.cap"]),
    ("duplicate_id_violated_then_satisfied", []), ("duplicate_id_both_satisfied", []), ("unique_ids", [])])
async def test_oi65_current_behaviour_validate_plan_records_a_round_0_regression_only_for_the_downgrade(
        tmp_path, name, regressions):
    """OI-65 mechanism (the discriminating check): one ``validate_plan`` call (revision 0) on a fresh ledger appends a
    regression event exactly when a repeated subtask id downgrades the same obligation SATISFIED -> VIOLATED; no
    duplicate-id refusal precedes the recording."""
    ledger = ObligationLedger()
    await validate_plan(EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=_PLANS[name]),
                        workspace_path=str(tmp_path), obligation_ledger=ledger, revision=0,
                        require_semantic_contracts=True)
    assert [event.obligation_id for event in ledger.regressions] == regressions
