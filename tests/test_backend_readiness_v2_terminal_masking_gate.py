"""BACKEND-READINESS-V2 (S4_A, owner decision 2026-10-12 item 6): the exception-masking rule is judged on the FINAL
candidate, not only at the two staging sites in attempt.py.

The staging guard sees what the Developer response stages. Anything else that can leave Java text in the isolated candidate
(the opt-in self-correction micro-loop's tool writes, a TOOL subtask, a shell command, an anchored edit applied elsewhere)
never passed through it. The terminal ``semantic_safety`` gate judges the exact batch the commit will write
(``plan_terminal_writes``) against the real workspace, so the rule holds whatever route produced the bytes. Every route
below is a real writer; the gate must reject the same masking result from each of them, accept the controls, and fail
closed when it cannot decide."""
import asyncio
import subprocess
from pathlib import Path

import pytest

from kriya.config import AppConfig
from kriya.policy.filesystem import AuthorizedFileWriter
from kriya.static_analysis.service import StaticAnalysisCandidate, StaticAnalysisService
from kriya.workflow.commit_service import plan_terminal_writes
from kriya.workflow.edit_safety import apply_anchored_edits, read_file_revision
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.migration import MigrationResolution, MigrationResolutionStatus
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.requirements import derive_requirements
from kriya.workflow.state import GenerationState
from kriya.workflow.terminal_gate_service import (
    TerminalGateRequest,
    TerminalGateService,
    TerminalGateValidators,
)
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow import _run_semantic_safety_gate

PATH = "src/main/java/demo/PathCompiler.java"
BASE = '''package demo;

public class PathCompiler {
    public static String compile(String path) {
        try {
            return read(path);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    static String read(String p) {
        return p;
    }
}
'''
OLD_CLAUSE = "        } catch (Exception e) {\n            throw new IllegalStateException(e);\n        }\n"


def _with_clause(clause: str, body: str, base: str = BASE) -> str:
    assert OLD_CLAUSE in base
    return base.replace(OLD_CLAUSE, f"        }} catch ({clause}) {{\n{body}\n        }}\n")


MASKING = _with_clause("Throwable e", "            throw new IllegalStateException(e);")  # the measured S4_A shape
FIXED = BASE.replace("return read(path);", "return read(path).trim();")


def _validators():
    async def verify(*args, **kwargs):
        del args, kwargs
        return []

    return TerminalGateValidators(
        find_migration_incomplete=lambda *args, **kwargs: None,
        validate_stack_contract_artifacts=lambda *args, **kwargs: None,
        enforce_preserved_reference_terminal_integrity=lambda ledger, root: None,
        blocking_requirements=lambda *args, **kwargs: [],
        verify_original_requirements=verify,
        evaluate_static_analysis=StaticAnalysisService(AppConfig()).evaluate_candidate,
    )


def _trees(tmp_path, *, base=BASE, path=PATH):
    """A real workspace and an isolated candidate that both start as ``base``; returns (workspace, candidate)."""
    roots = []
    for name in ("workspace", "candidate"):
        root = tmp_path / name
        (root / path).parent.mkdir(parents=True)
        (root / path).write_text(base)
        roots.append(root)
    return roots


def _gate(tmp_path, workspace, candidate, *, action=FileAction.MODIFY, path=PATH, validators=None, also=()):
    """``also``: further planned MODIFY paths that exist in both trees."""
    plan = EngineeringPlan(plan_id="masking-final-state", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="fix", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path=path, action=action)] + [PlannedFile(path=p, action=FileAction.MODIFY) for p in also],
    )])
    revisions = {p: read_file_revision(str(workspace / p)) for p in (path, *also)}
    goal = "Fix the crash."
    request = TerminalGateRequest(
        plan=plan, goal=goal, candidate_root=str(candidate), workspace_path=str(workspace),
        migration_resolution=MigrationResolution(MigrationResolutionStatus.NOT_APPLICABLE),
        obligation_ledger=ObligationLedger(), requirement_set=derive_requirements(goal), autonomy=None,
        spec_compliance=None, milestone_id="m1",
        commit_batch=lambda: plan_terminal_writes(plan, str(candidate), str(workspace), revisions),
        static_analysis_candidate=StaticAnalysisCandidate(
            materialize=list, workspace_path=str(workspace), run_id="run", unit_id="m1"),
    )
    events = []

    async def emit(gate, status, reason):
        events.append((gate, status, reason))

    report = asyncio.run(TerminalGateService(validators or _validators()).run(request, emit))
    return report, events


def _route_whole_file(candidate, content):
    (candidate / PATH).write_text(content)


def _route_anchored_edit(candidate, content):
    del content  # the production anchored-edit engine builds the candidate from the pristine source
    edit = {"search": "        } catch (Exception e) {", "replace": "        } catch (Throwable e) {"}
    original = (candidate / PATH).read_text()
    (candidate / PATH).write_text(apply_anchored_edits(original, [edit], original))


def _route_tool_mediated(candidate, content):
    """The writer the opt-in self-correction micro-loop's patch tool uses (self_correction.py, AuthorizedFileWriter)."""
    full = str(candidate / PATH)
    AuthorizedFileWriter(str(candidate)).commit_file(full, content, expected_revision=read_file_revision(full))


def _route_shell_mediated(candidate, content):
    subprocess.run(["sh", "-c", f"cat > '{candidate / PATH}'"], input=content.encode(), check=True)


ROUTES = {
    "whole_file_replacement": _route_whole_file,
    "anchored_edit": _route_anchored_edit,
    "tool_mediated_authorized_writer": _route_tool_mediated,
    "shell_mediated": _route_shell_mediated,
}


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_masking_result_blocks_the_commit_whatever_route_wrote_it(tmp_path, route):
    workspace, candidate = _trees(tmp_path)
    ROUTES[route](candidate, MASKING)
    assert "catch (Throwable e)" in (candidate / PATH).read_text()  # the route really produced the masking candidate

    report, events = _gate(tmp_path, workspace, candidate)

    assert report.ran and not report.commit_eligible
    assert "UNSAFE_EXCEPTION_MASKING" in report.semantic_safety_gap and PATH in report.semantic_safety_gap
    assert [gate for gate, status, _ in events if status == "failed"] == ["semantic_safety"]
    assert dict(report.global_gaps())["global_semantic_safety_gap"] == report.semantic_safety_gap
    assert (workspace / PATH).read_text() == BASE  # a gate never writes the real workspace


def test_a_real_fix_with_no_masking_stays_commit_eligible(tmp_path):
    workspace, candidate = _trees(tmp_path)
    _route_whole_file(candidate, FIXED)
    report, events = _gate(tmp_path, workspace, candidate)
    assert report.commit_eligible and report.semantic_safety_gap is None
    assert ("semantic_safety", "passed", None) in events


@pytest.mark.parametrize("name,candidate_text", [
    ("propagates_the_error", _with_clause("Throwable e", "            throw e;")),
    ("rethrows_as_an_error", _with_clause("StackOverflowError e", "            throw new AssertionError(e);")),
    ("ordinary_exception_swallowed", _with_clause("RuntimeException e", "            return null;")),
])
def test_controls_that_are_not_error_masking_pass(tmp_path, name, candidate_text):
    del name
    workspace, candidate = _trees(tmp_path)
    _route_whole_file(candidate, candidate_text)
    report, _events = _gate(tmp_path, workspace, candidate)
    assert report.semantic_safety_gap is None and report.commit_eligible


def test_a_pre_existing_masking_clause_that_the_candidate_keeps_is_not_a_finding(tmp_path):
    base = _with_clause("Throwable e", "            return null;")
    workspace, candidate = _trees(tmp_path, base=base)
    _route_whole_file(candidate, base.replace("return read(path);", "return read(path).trim();"))
    report, _events = _gate(tmp_path, workspace, candidate)
    assert report.semantic_safety_gap is None and report.commit_eligible


def test_a_second_masking_clause_beyond_the_workspace_baseline_is_a_finding(tmp_path):
    base = _with_clause("Throwable e", "            return null;")
    workspace, candidate = _trees(tmp_path, base=base)
    extra = base.replace("    static String read(String p) {\n        return p;\n    }\n",
                         "    static String read(String p) {\n        try { return p; } catch (Throwable t) { return null; }\n    }\n")
    assert extra != base
    _route_whole_file(candidate, extra)
    report, _events = _gate(tmp_path, workspace, candidate)
    assert "UNSAFE_EXCEPTION_MASKING" in report.semantic_safety_gap and not report.commit_eligible


def test_every_masking_file_of_the_batch_is_reported_not_only_the_first(tmp_path):
    workspace, candidate = _trees(tmp_path)
    second = "src/main/java/demo/Other.java"
    for root in (workspace, candidate):
        (root / second).write_text(BASE.replace("PathCompiler", "Other"))
    _route_whole_file(candidate, MASKING)
    (candidate / second).write_text(_with_clause("Error e", "            return null;", BASE.replace("PathCompiler", "Other")))
    report, _events = _gate(tmp_path, workspace, candidate, also=(second,))
    assert PATH in report.semantic_safety_gap and second in report.semantic_safety_gap


def test_a_new_java_file_is_judged_against_no_baseline(tmp_path):
    workspace, candidate = _trees(tmp_path)
    new_path = "src/main/java/demo/Extra.java"
    (candidate / new_path).write_text("package demo;\nclass Extra {\n    void run() { try { go(); } catch (Throwable t) { } }\n    void go() {}\n}\n")
    report, _events = _gate(tmp_path, workspace, candidate, action=FileAction.CREATE, path=new_path)
    assert "UNSAFE_EXCEPTION_MASKING" in report.semantic_safety_gap and not report.commit_eligible


def test_a_deleted_file_and_a_non_java_file_are_not_judged(tmp_path):
    workspace, candidate = _trees(tmp_path)
    (candidate / PATH).unlink()
    report, _events = _gate(tmp_path, workspace, candidate, action=FileAction.DELETE)
    assert report.semantic_safety_gap is None

    workspace2, candidate2 = _trees(tmp_path / "py", base="x = 1\n", path="app.py")
    (candidate2 / "app.py").write_text("try:\n    pass\nexcept BaseException:\n    pass\ncatch (Throwable e) { }\n")
    report2, _events2 = _gate(tmp_path / "py", workspace2, candidate2, path="app.py")
    assert report2.semantic_safety_gap is None and report2.commit_eligible


def test_a_check_that_raises_is_an_indeterminate_failure_never_a_pass(tmp_path, monkeypatch):
    workspace, candidate = _trees(tmp_path)
    _route_whole_file(candidate, FIXED)

    def explode(*args, **kwargs):
        raise RuntimeError("masking check exploded")

    monkeypatch.setattr("kriya.workflow.exception_masking.find_unsafe_exception_masking", explode)
    report, events = _gate(tmp_path, workspace, candidate)
    assert not report.commit_eligible
    assert report.semantic_safety_gap.startswith("SEMANTIC SAFETY FINAL-STATE CHECK INDETERMINATE")
    assert "RuntimeError: masking check exploded" in report.semantic_safety_gap
    assert [gate for gate, status, _ in events if status == "failed"] == ["semantic_safety"]


def test_the_gate_runs_after_static_analysis_and_before_terminal_obligations(tmp_path):
    workspace, candidate = _trees(tmp_path)
    _route_whole_file(candidate, FIXED)
    _report, events = _gate(tmp_path, workspace, candidate)
    gates = [gate for gate, _status, _reason in events]
    assert gates.index("static_analysis") + 1 == gates.index("semantic_safety") == gates.index("terminal_obligations") - 1


def test_the_gate_reads_only_the_batch_and_never_the_candidate_directory_listing():
    """Structural: the check is bound to the commit batch (what is written), so a stray undeclared file in the sandbox that the
    commit never writes cannot fail a run, and a planned file cannot escape by being unlisted."""
    source = Path(__file__).resolve().parent.parent / "kriya" / "workflow" / "terminal_gate_service.py"
    text = source.read_text()
    start = text.index("def _semantic_safety_gap")
    body = text[start:text.index("\n    def ", start + 10)]
    assert "request.commit_batch()" in body and "os.walk" not in body and "listdir" not in body


# --- the direct / milestone pre-apply boundary: the same rule on the same kind of final batch ---------------------------

def _direct_state(content_before: str) -> GenerationState:
    state = GenerationState()
    state.attempt_number = 2
    state.all_files_written = [PATH]
    state.all_original_contents = {PATH: content_before}
    state.all_original_raw = {PATH: content_before.encode()}
    return state


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_direct_boundary_refuses_a_masking_result_whatever_route_wrote_it(tmp_path, route):
    workspace, candidate = _trees(tmp_path)
    ROUTES[route](candidate, MASKING)
    state = _direct_state(BASE)
    with pytest.raises(QualityGateFailure) as stop:
        _run_semantic_safety_gate(state, worktree_path=str(candidate), workspace_path=str(workspace))
    failure = stop.value.failure
    assert failure.type == "unsafe_exception_masking" and "UNSAFE_EXCEPTION_MASKING" in failure.message
    assert failure.likely_files == [PATH] and failure.attempt == 2
    assert state.gate_outcomes  # the refusal is recorded as a gate outcome


def test_direct_boundary_judges_in_place_runs_against_the_run_start_bytes(tmp_path):
    """In-place generation has already overwritten the workspace file, so the baseline must be the captured original."""
    workspace, _candidate = _trees(tmp_path)
    (workspace / PATH).write_text(MASKING)  # the in-place write
    with pytest.raises(QualityGateFailure):
        _run_semantic_safety_gate(_direct_state(BASE), worktree_path=str(workspace), workspace_path=str(workspace))


def test_direct_boundary_passes_a_real_fix_and_a_kept_pre_existing_clause(tmp_path):
    workspace, candidate = _trees(tmp_path)
    _route_whole_file(candidate, FIXED)
    _run_semantic_safety_gate(_direct_state(BASE), worktree_path=str(candidate), workspace_path=str(workspace))

    kept = _with_clause("Throwable e", "            return null;")
    workspace2, candidate2 = _trees(tmp_path / "kept", base=kept)
    (candidate2 / PATH).write_text(kept.replace("return read(path);", "return read(path).trim();"))
    _run_semantic_safety_gate(_direct_state(kept), worktree_path=str(candidate2), workspace_path=str(workspace2))


def test_end_to_end_direct_workflow_the_final_gate_stops_what_the_staging_guard_does_not_see(tmp_path):
    """Whole direct workflow, gates stubbed green, with the STAGING guard disabled (standing in for any route that never
    passes through it): the pre-apply semantic-safety gate alone must refuse the widening and leave the file untouched;
    the same setup must still accept an ordinary correct edit."""
    from unittest.mock import patch

    from test_backend_readiness_v2_exception_masking import BAD, GOOD, _end_to_end
    from test_backend_readiness_v2_exception_masking import BASE as E2E_BASE

    with patch("kriya.workflow.attempt._reject_exception_masking", new=lambda *args, **kwargs: None):
        passed, failures, final, _sent = _end_to_end(tmp_path / "bad", BAD)
        assert passed is False and failures.count("unsafe_exception_masking") >= 1
        assert final == E2E_BASE
        passed, failures, final, _sent = _end_to_end(tmp_path / "good", GOOD)
        assert "unsafe_exception_masking" not in failures and passed is True and "IllegalArgumentException" in final


def test_direct_boundary_leaves_an_unmaterializable_candidate_to_the_commit_refusal(tmp_path):
    workspace, candidate = _trees(tmp_path)
    (candidate / PATH).unlink()  # the written file is gone: no batch can be bound
    _run_semantic_safety_gate(_direct_state(BASE), worktree_path=str(candidate), workspace_path=str(workspace))


def test_in_place_enforce_has_no_pre_run_text_so_any_masking_clause_fails_closed(tmp_path):
    """candidate_root == workspace_path: the file already is the candidate, so a baseline read from it would hide the
    widening. The gate judges with no baseline instead."""
    workspace, _candidate = _trees(tmp_path)
    (workspace / PATH).write_text(MASKING)
    report, _events = _gate(tmp_path, workspace, workspace)
    assert "UNSAFE_EXCEPTION_MASKING" in report.semantic_safety_gap and not report.commit_eligible
    (workspace / PATH).write_text(FIXED)
    clean, _events = _gate(tmp_path, workspace, workspace)
    assert clean.semantic_safety_gap is None
