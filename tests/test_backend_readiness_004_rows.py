"""BACKEND-READINESS-004 registry sweep: the deterministic reproducers and
regressions of the rows fixed in the sweep (one section per row id). Each
reproduces the measured shape the row recorded, not a summary of it.
"""
import asyncio
import json
import os
import re
import sqlite3
import subprocess
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from _strict_doubles import strict_config, strict_kernel
from click.testing import CliRunner
from test_embedding_contract_001 import _client, _Server
from test_embedding_contract_001 import _run as _run_coro
from test_prd020_milestone_requirements import Transport, _probe
from test_prompt_budget_fit_001c import _config, _run_record, _trace_rows, _workspace

from kriya.agents.contracts import escapes_workspace, parse_file_list, partition_file_list_escapes
from kriya.cli import main
from kriya.core import model_runtime
from kriya.core import release_identity as ri
from kriya.core.llm import LLMClient
from kriya.core.role_metrics import current_model_role
from kriya.core.state_paths import trace_db_path
from kriya.core.trace import TraceLogger
from kriya.memory.embedding import MAX_SEGMENT_DEPTH, EmbeddingInputTooLongError, embed_chunks
from kriya.metrics.derive import derive_metrics
from kriya.metrics.evidence import load_trace_runs
from kriya.metrics.report import build_report, render_markdown
from kriya.tools import lsp as lsp_module
from kriya.tools.lsp import JdtlsClient
from kriya.tools.process import spawn_subprocess_exec_fail_closed
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirement_contract import CONTRACT_FORMAT, load_requirement_contract, requirement_set_for
from kriya.workflow.requirements import (
    CONTRACT_REQUIREMENTS_SOURCE,
    DERIVED_REQUIREMENTS_SOURCE,
    derive_requirements,
    requirement_obligation_id,
    requirement_verdict_details,
    seed_requirement_obligations,
)
from kriya.workflow.workflow import FINAL_REVIEW_BACKEND_ERROR, WorkflowEngine
from kriya.workflow.workflow_controller import PLAN_SCOPE_REVISION_REQUIRED, subtask_scope_conflict_report
from kriya.workflow.worktree import create_git_worktree


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(root, files):
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@e")
    _git(root, "config", "user.name", "T")
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    return root


# ------------------------------------------------- OBS-2-EMBEDDING-INPUT-TOO-LONG-INDEX-GAP

GRAPH_HTML_LINE_BYTES = 934_638  # MEASURED: the longest line of Graphify's worked/rsl-siege-manager/graph.html
GRAPH_HTML_LINES = 304  # MEASURED: its line count (1,846,390 bytes in all)
NOMIC_SERVED_CONTEXT = 8192


def _graph_html_chunk():
    """The measured shape: a 100-line analyzer chunk (chunk_file_syntactically's
    max_lines) of graph.html holding its 934 KB single line."""
    lines = [f"<div id='n{i}'></div>" for i in range(99)]
    lines.insert(40, "<script>" + "x" * (GRAPH_HTML_LINE_BYTES - len("<script></script>")) + "</script>")
    return {"text": "\n".join(lines), "start": 1, "end": 100}


def test_obs2_the_measured_graph_html_chunk_needed_more_estimate_halvings_than_the_old_shared_bound():
    """The arithmetic of the measured refusal: isolating the single line takes
    7 line halvings and fitting it 6 more - 13, over the bound of 12 that
    counted both before."""
    chunk = _graph_html_chunk()
    limit_bytes = NOMIC_SERVED_CONTEXT * 2.0
    line_halvings = (100 - 1).bit_length()  # 7: 100 lines -> 1 line
    byte_halvings = 0
    size = GRAPH_HTML_LINE_BYTES
    while size > limit_bytes:
        size /= 2
        byte_halvings += 1
    assert line_halvings + byte_halvings == 13 > MAX_SEGMENT_DEPTH
    assert len(chunk["text"].encode("utf-8")) > limit_bytes


def test_obs2_the_measured_graph_html_chunk_is_embedded_by_estimate_driven_segmentation():
    """Post-fix: every piece is an estimated fit, the provider sees no over-long
    input, nothing is dropped, the admission counter stays 0 - and the chunk
    keeps its identity (one parent, ordered segments, the source span)."""
    chunk = _graph_html_chunk()
    server = _Server(max_chars=10_000_000)
    client = _client()
    with patch("httpx.AsyncClient.post", new=server.post):
        segments = _run_coro(embed_chunks(client, [chunk], served_context=NOMIC_SERVED_CONTEXT))
    assert len(segments) > 60 and client.admission_misses == 0
    assert [s.segment_index for s in segments] == list(range(len(segments)))
    assert all((s.parent_chunk, s.start_line, s.end_line) == (0, 1, 100) for s in segments)
    assert "".join(s.text for s in segments).replace("\n", "") == chunk["text"].replace("\n", "")
    limit = NOMIC_SERVED_CONTEXT * 2 + len("search_document: ")
    assert all(len(body["input"][0]) <= limit for _, body in server.requests)


def test_obs2_a_provider_that_refuses_everything_still_ends_in_a_bounded_typed_error():
    """The bound now counts the provider's refusals of an estimated fit: a
    provider refusing every piece ends typed after MAX_SEGMENT_DEPTH + 1
    refusals along one path, never a loop and never a legacy fallback."""
    server = _Server(max_chars=0)
    client = _client()
    # 1 MB on one line: the estimate halves it to a fit first (no miss), then
    # the provider's 13 refusals along the first path end it while the piece
    # is still splittable (a shorter text would simply run out of characters).
    work = embed_chunks(client, [{"text": "a" * 1_000_000, "start": 1, "end": 1}], served_context=10_000)
    with patch("httpx.AsyncClient.post", new=server.post), pytest.raises(EmbeddingInputTooLongError):
        _run_coro(work)
    assert client.admission_misses == MAX_SEGMENT_DEPTH + 1


# ------------------------------------------------- RELEASE-IDENTITY-WHEEL-001

def test_an_installed_wheel_reports_its_embedded_commit_not_unavailable(tmp_path):
    no_checkout = tmp_path / "site-packages-kriya"
    no_checkout.mkdir()
    embedded = {"commit": "abc123def4567890abc123def4567890abc12345", "tree": "t", "dirty": False}
    with patch("kriya.build_info.embedded_build_info", return_value=embedded):
        assert ri.kriya_revision(str(no_checkout)) == {"revision": embedded["commit"], "dirty": False}
    with patch("kriya.build_info.embedded_build_info", return_value={"commit": "UNKNOWN", "tree": "UNKNOWN", "dirty": None}):
        assert ri.kriya_revision(str(no_checkout)) == {"revision": ri.UNAVAILABLE, "dirty": None}


def test_a_checkout_still_reads_git_never_the_embedded_identity(tmp_path):
    source = _repo(tmp_path / "src", {"a.py": "x = 1\n"})
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, check=True).stdout.strip()
    with patch("kriya.build_info.embedded_build_info", side_effect=AssertionError("a checkout never reads the embedded identity")):
        assert ri.kriya_revision(str(source)) == {"revision": head, "dirty": False}


# ------------------------------------------------- WORKTREE-SYNC-BYTECODE-CACHE-001

def test_interpreter_caches_are_not_synced_into_the_candidate_worktree(tmp_path):
    """The measured repository: __pycache__ not ignored, the baseline gate's
    bytecode untracked beside untracked source. The worktree gets the source,
    never the bytecode (nor pytest's cache)."""
    ws = _repo(tmp_path / "ws", {"pkg/__init__.py": "", "pkg/mod.py": "def f():\n    return 1\n"})
    (ws / "pkg" / "__pycache__").mkdir()
    (ws / "pkg" / "__pycache__" / "mod.cpython-312-pytest-8.0.0.pyc").write_bytes(b"\x00stale bytecode")
    (ws / ".pytest_cache" / "v" / "cache").mkdir(parents=True)
    (ws / ".pytest_cache" / "v" / "cache" / "lastfailed").write_text("{}")
    (ws / "pkg" / "new.py").write_text("NEW = 1\n")  # untracked source still travels
    (ws / "pkg" / "mod.py").write_text("def f():\n    return 2\n")  # a modified tracked file too
    worktree = create_git_worktree(str(ws))
    assert (worktree != str(ws)) and os.path.isdir(worktree)
    assert (ws / "pkg" / "__pycache__" / "mod.cpython-312-pytest-8.0.0.pyc").exists()  # the workspace keeps its own
    assert not os.path.exists(os.path.join(worktree, "pkg", "__pycache__"))
    assert not os.path.exists(os.path.join(worktree, ".pytest_cache"))
    assert open(os.path.join(worktree, "pkg", "new.py")).read() == "NEW = 1\n"
    assert open(os.path.join(worktree, "pkg", "mod.py")).read() == "def f():\n    return 2\n"


# ------------------------------------------------- OBS-3-CONTRACT-PROVENANCE-LABEL

def test_the_ledger_and_verdict_detail_name_the_operator_contract_when_one_is_bound(tmp_path):
    goal = "Add a parser. Keep the existing tests passing."
    derived = derive_requirements(goal)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, derived)
    detail = requirement_verdict_details(ledger, derived)
    assert {d["source"] for d in detail.values()} == {DERIVED_REQUIREMENTS_SOURCE}

    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps({"format": CONTRACT_FORMAT, "requirements": [
        {"id": "REQ-1", "text": "The parser accepts ISO dates.", "kind": "requirement"},
        {"id": "REQ-2", "text": "The existing tests keep passing.", "kind": "constraint"},
    ]}))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    contract = load_requirement_contract(str(contract_path), goal, state_root=str(tmp_path / "state"),
                                         workspace=str(workspace))
    bound = requirement_set_for(goal, contract)
    assert bound.contract_digest == contract.digest
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, bound)
    detail = requirement_verdict_details(ledger, bound)
    assert {d["source"] for d in detail.values()} == {CONTRACT_REQUIREMENTS_SOURCE}
    record = ledger.current(requirement_obligation_id(bound.ids[0]))
    assert record.source == CONTRACT_REQUIREMENTS_SOURCE and record.evidence["source"] == "operator_contract"
    # The label changes nothing else: same digest with or without it.
    assert bound.digest == requirement_set_for(goal, contract).digest


# ------------------------------------------------- PLAN-SCOPE-CONFLICT-REASON-TYPING-001

@pytest.mark.parametrize("conflict,code,text", [
    ({"classification": "verification_contract_defect", "reason_code": "VERIFICATION_CONTRACT_REVISION_REQUIRED",
      "reason": "the named test oracle changed", "required_files": [], "allowed_files": ["a.py"]},
     "VERIFICATION_CONTRACT_REVISION_REQUIRED",
     "subtask repair requires approved-plan revision (VERIFICATION_CONTRACT_REVISION_REQUIRED): the named test oracle changed"),
    ({"classification": "runtime_plan_gap", "reason_code": "RUNTIME_PLAN_GAP", "reason": "missing module app.db",
      "required_files": []},
     "RUNTIME_PLAN_GAP", "subtask repair requires approved-plan revision (RUNTIME_PLAN_GAP): missing module app.db"),
    ({"reason_code": "PLAN_TARGET_RELOCALIZATION_REQUIRED", "reason": "", "required_files": []},
     "PLAN_TARGET_RELOCALIZATION_REQUIRED",
     "subtask repair requires approved-plan revision (PLAN_TARGET_RELOCALIZATION_REQUIRED): no reason recorded"),
])
def test_a_scope_conflict_with_its_own_reason_code_and_no_files_reports_that_code(conflict, code, text):
    error, codes = subtask_scope_conflict_report(conflict)
    assert error == text
    assert codes == (PLAN_SCOPE_REVISION_REQUIRED, code)


def test_a_grounded_file_conflict_keeps_the_files_sentence_and_adds_its_own_code():
    conflict = {"classification": "plan_scope_defect", "reason_code": "PLANNED_PREREQUISITE_OWNER_REQUIRED",
                "reason": "pom.xml is owned by s1", "required_files": ["pom.xml"], "allowed_files": ["App.java"]}
    error, codes = subtask_scope_conflict_report(conflict)
    assert error == ("subtask repair requires approved-plan scope revision; grounded required files ['pom.xml'] "
                     "are outside this stage's allowed files ['App.java']")
    assert codes == (PLAN_SCOPE_REVISION_REQUIRED, "PLANNED_PREREQUISITE_OWNER_REQUIRED")
    legacy = {"required_files": ["a.py"], "allowed_files": []}  # no reason_code: the pre-existing shape, unchanged
    assert subtask_scope_conflict_report(legacy) == (
        "subtask repair requires approved-plan scope revision; grounded required files ['a.py'] are outside "
        "this stage's allowed files []", (PLAN_SCOPE_REVISION_REQUIRED,))


# ------------------------------------------------- KNOWN-TARGET-CAPACITY-REFUSAL-EVIDENCE-001

def test_a_capacity_omission_names_the_collective_condition_and_every_targets_minimum(tmp_path):
    from test_known_target_multi_target_starvation_001 import (
        EXC_EDIT,
        PY_EXC,
        PY_EXC_MEMBERS,
        PY_EXC_SOURCE,
        PY_URLS,
        PY_URLS_MEASURED,
        URLS_EDIT,
        _first,
        _measured_shape,
        _run,
    )

    from kriya.workflow.context_budget import REASON_MINIMUM_AUTHORITY_UNFIT

    result, events, developer = _run(tmp_path, {PY_EXC: PY_EXC_SOURCE, PY_URLS: PY_URLS_MEASURED},
                                     {PY_EXC: PY_EXC_MEMBERS}, {PY_EXC: EXC_EDIT, PY_URLS: URLS_EDIT})
    _measured_shape(events)
    assert result.get("failure_category") == "context_edit_protocol_unsatisfiable" and developer == []
    package = _first(events, "context.known_target_package")
    [entry] = [o for o in package["omitted"] if o["reason"] == REASON_MINIMUM_AUTHORITY_UNFIT]
    assert entry["path"] == PY_URLS
    assert entry["minimum_tokens"] == entry["estimated_tokens"] > entry["available_protected_tokens"] > 0
    assert set(entry["minimum_tokens_by_target"]) == {PY_EXC, PY_URLS}
    assert entry["minimum_tokens_by_target"][PY_URLS] == entry["minimum_tokens"]
    assert entry["minimum_tokens_by_target"][PY_EXC] > 0  # the admitted members' exact cost
    assert entry["required_minimum_tokens_total"] == sum(entry["minimum_tokens_by_target"].values())
    assert entry["refusal_reason"] == "this target's minimum authoritative unit alone exceeds the protected room"


# ------------------------------------------------- MCP-APPROVAL-PATH-TRACEBACK-001

def _mcp_invoke(tmp_path, monkeypatch, store_home, *args):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    monkeypatch.chdir(ws)
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(store_home))
    cfg = strict_config(mcp={"fake": {"command": "/usr/bin/false"}}, logging={"file_enabled": False})
    discover = AsyncMock(side_effect=AssertionError("no MCP server may start before the store path is accepted"))
    with patch("kriya.cli.load_config", return_value=cfg), patch("kriya.cli._discover_mcp_tools", new=discover):
        result = CliRunner().invoke(main, ["mcp", *args])
    return result, discover


@pytest.mark.parametrize("command", [["inspect"], ["approve", "fake.tool", "--confirm"], ["revoke", "fake.tool"]])
def test_an_in_workspace_mcp_approval_store_is_a_typed_refusal_before_any_server_starts(tmp_path, monkeypatch, command):
    result, discover = _mcp_invoke(tmp_path, monkeypatch, tmp_path / "ws" / ".mcp", *command)
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    assert "[MCP_TRUST_PATH_INSIDE_WORKSPACE]" in result.output and "Remediation: Point KRIYA_MCP_APPROVAL_HOME" in result.output
    assert discover.await_count == 0
    assert not (tmp_path / "ws" / ".mcp").exists()


def test_a_store_outside_the_workspace_proceeds_to_discovery(tmp_path, monkeypatch):
    """Control: the refusal is the store path, nothing else in the command."""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(tmp_path / "outside"))
    cfg = strict_config(mcp={"fake": {"command": "/usr/bin/false"}}, logging={"file_enabled": False})
    kernel = strict_kernel(cfg)
    kernel.stop = AsyncMock()
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("kriya.cli._discover_mcp_tools", new=AsyncMock(return_value=(kernel, []))):
        result = CliRunner().invoke(main, ["mcp", "inspect"])
    assert result.exit_code == 0 and "No MCP tools discovered." in result.output, result.output


# ------------------------------------------------- FINAL-REVIEW-BACKEND-ERROR-001

class _ReviewerEndpointDown(Transport):
    """The Reviewer's endpoint fails at transport level after the commit; every other role answers."""

    def __init__(self):
        super().__init__()
        self.failed = 0

    async def __call__(self, client, model, system_prompt, user_prompt, *args, **kwargs):
        if current_model_role() == "reviewer":
            self.failed += 1
            raise httpx.ConnectError("All connection attempts failed")
        return await super().__call__(client, model, system_prompt, user_prompt, *args, **kwargs)


def test_a_reviewer_endpoint_failure_after_the_commit_is_a_typed_non_success(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe)
    model_runtime.clear_model_runtime_cache()
    monkeypatch.chdir(workspace)
    cfg = _config(tmp_path)
    transport = _ReviewerEndpointDown()
    results = []
    real_run = WorkflowEngine.run_generation_workflow

    async def capturing(self, *a, **kw):
        result = await real_run(self, *a, **kw)
        results.append(result)
        return result

    with patch("kriya.cli.load_config", return_value=cfg), patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(WorkflowEngine, "run_generation_workflow", new=capturing):
        cli = CliRunner().invoke(main, ["generate", "build M1: create m1.py", "-y"])

    assert cli.exception is None or isinstance(cli.exception, SystemExit), cli.output  # never a raw exception
    assert cli.exit_code != 0 and transport.failed == 1  # not retried
    result = results[-1]
    assert result["quality_gates_passed"] is False and result["failure_category"] == "final_review_refused"
    assert result["candidate_gates_passed"] and result["terminal_regression_passed"]
    refusal = result["final_review_refusal"]
    assert refusal["reason_code"] == FINAL_REVIEW_BACKEND_ERROR and refusal["exception_type"] == "ConnectError"
    assert "All connection attempts failed" in refusal["detail"]
    assert refusal["candidate_applied"] is True and refusal["rolled_back"] is False
    record = _run_record(workspace)
    assert refusal["committed_work_units"] == record.committed_work_units() == ["direct"]
    assert record.terminal_status != "SUCCESS"
    assert (workspace / "m1.py").read_text().strip() == "VALUE = 'm1.py'"  # applied and committed, not rolled back
    status, category, events = _trace_rows(cfg)[-1]
    assert (status, category) == ("failure", "final_review_refused")
    [refused] = [e["details"] for e in json.loads(events) if e["kind"] == "review.refused"]
    assert refused["reason_code"] == FINAL_REVIEW_BACKEND_ERROR
    assert "Files applied to workspace and committed (direct), not rolled back: m1.py" in cli.output


# ------------------------------------------------- PLAT-LSP-TREE-KILL-001

def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_releasing_the_jdtls_client_kills_a_grandchild_the_launcher_started(tmp_path):
    """The measured class: jdtls's launcher starts a JVM child; killing the
    direct PID alone left it running past a timeout. The client owns the tree."""
    client = JdtlsClient(str(tmp_path), "/fake/jdtls")
    pidfile = tmp_path / "grandchild.pid"
    client.process = await spawn_subprocess_exec_fail_closed(
        "/bin/sh", "-c", f"sleep 300 & echo $! > {pidfile}; wait",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while not pidfile.exists() or not pidfile.read_text().strip():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.05)
    grandchild = int(pidfile.read_text().strip())
    assert _alive(grandchild)
    try:
        await client._release()
        deadline = time.monotonic() + 5
        while _alive(grandchild) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not _alive(grandchild)
        assert client.process.returncode is not None  # the leader was reaped
    finally:
        for pid in (grandchild,):
            if _alive(pid):
                os.kill(pid, 9)


@pytest.mark.asyncio
async def test_releasing_an_already_exited_jdtls_is_not_an_error(tmp_path):
    client = JdtlsClient(str(tmp_path), "/fake/jdtls")
    client.process = await spawn_subprocess_exec_fail_closed("/bin/sh", "-c", "exit 0", stdin=asyncio.subprocess.PIPE)
    await client.process.wait()
    await client._release()


def test_jdtls_is_spawned_through_the_process_tree_port_only():
    source = open(lsp_module.__file__, encoding="utf-8").read()
    assert "asyncio.create_subprocess_exec(" not in source
    assert "spawn_subprocess_exec_fail_closed(" in source and "terminate_process_tree(" in source


# ------------------------------------------------- TRACE-ENFORCE-SUBTASK-LINKAGE-001

def test_subtask_trace_rows_name_their_enforce_run_and_metrics_group_them(tmp_path):
    db = tmp_path / "traces.db"
    logger = TraceLogger(str(db))
    for run_id, ok in (("s1run", True), ("s2run", False)):
        logger.log_run(run_id=run_id, goal="g", duration_sec=1.0, attempts=1, status="success" if ok else "failure",
                       files_modified=[], gate_outcomes=[{"attempt": 1, "type": "compile", "success": ok}],
                       enforce_run_id="e1.enforce")
    logger.log_run(run_id="direct", goal="g", duration_sec=1.0, attempts=1, status="success", files_modified=[])
    logger.log_run(run_id="e1.enforce", goal="g", duration_sec=2.0, attempts=0, status="failure", files_modified=[])
    with sqlite3.connect(db) as conn:
        rows = dict(conn.execute("SELECT run_id, enforce_run_id FROM runs").fetchall())
    assert rows == {"s1run": "e1.enforce", "s2run": "e1.enforce", "direct": None, "e1.enforce": None}
    runs = load_trace_runs(str(db))
    assert {r.run_id: r.enforce_run_id for r in runs} == rows
    content = derive_metrics(runs)
    assert list(content["by_enforce_run"]) == ["e1.enforce"]
    block = content["by_enforce_run"]["e1.enforce"]
    assert block["runs"] == 2 and block["final_verified_success"]["numerator"] == 1
    assert content["outcomes"]["enforce"]["runs"] == 1  # the terminal row is still counted once, as before
    markdown = render_markdown(build_report(runs, generated={"at": "t"}))
    assert "## By enforce run" in markdown and "| `e1.enforce` | 2 |" in markdown


def test_a_legacy_traces_db_without_the_column_still_loads(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, timestamp TEXT, goal TEXT, duration_sec REAL, "
                     "attempts INTEGER, status TEXT, files_modified TEXT)")
        conn.execute("INSERT INTO runs VALUES ('old', '2026-01-01 00:00:00', 'g', 1.0, 1, 'success', '')")
    [run] = load_trace_runs(str(db))
    assert run.enforce_run_id is None and derive_metrics([run])["by_enforce_run"] == {}


@pytest.mark.asyncio
async def test_the_enforce_controller_passes_its_enforce_run_id_to_every_subtask_call(tmp_path):
    from test_auth_goal_contamination_001 import _plan_copying
    from test_workflow_controller import _workflow_engine

    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.workflow_controller import WorkflowController

    engine = _workflow_engine()
    engine.planner.run = AsyncMock(return_value="structured plan")
    calls = []

    async def generation(**kwargs):
        calls.append(kwargs)
        with open(os.path.join(kwargs["workspace_path"], "a.py"), "w", encoding="utf-8") as handle:
            handle.write("# generated\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["a.py"], "run_id": "unit"}

    engine.run_generation_workflow = AsyncMock(side_effect=generation)
    workspace = tmp_path / "live"
    workspace.mkdir()
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
               return_value=_plan_copying("create a.py")), \
         patch("kriya.workflow.workflow_controller.validate_plan",
               new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        await WorkflowController(engine).execute("create a.py", str(workspace), migration_mode="enforce")
    [kwargs] = calls
    with sqlite3.connect(trace_db_path(engine.kernel.config)) as conn:
        [(terminal,)] = conn.execute("SELECT run_id FROM runs WHERE run_id LIKE '%.enforce'").fetchall()
    assert kwargs["enforce_run_id"] == terminal and re.fullmatch(r".+\.enforce", terminal)


# ------------------------------------------------- ARCHITECT-FILE-LIST-ESCAPE-FALLBACK-001

HOSTILE = "Design: add sub.\n```json\n" + json.dumps(
    {"files": ["../outside/evil.py", "/abs/evil.py", "C:\\\\evil.py", "calc.py", "src/ok.py"]}) + "\n```\n"


def test_the_escaping_entries_are_partitioned_out_and_the_rest_validated():
    partition = partition_file_list_escapes(HOSTILE)
    assert partition.escaping == ("../outside/evil.py", "/abs/evil.py", "C:\\\\evil.py") and partition.error is None
    assert partition.files == ["calc.py", "src/ok.py"]
    assert parse_file_list(HOSTILE) == (None, parse_file_list(HOSTILE)[1])  # the strict parser still rejects the block
    assert "path-traversal" in parse_file_list(HOSTILE)[1]
    every = "```json\n" + json.dumps({"files": ["../a.py", "/b.py"]}) + "\n```"
    partition = partition_file_list_escapes(every)
    assert partition.files is None and partition.escaping == ("../a.py", "/b.py")
    assert partition.error == "every file-list entry escapes the workspace"
    assert partition_file_list_escapes("no block here").files is None
    assert partition_file_list_escapes("no block here").escaping == ()
    assert partition_file_list_escapes("```json\n{\"files\": [1, 2]}\n```").files is None
    assert [escapes_workspace(p) for p in ("../x", "/x", "C:/x", "a/../b", "a/b", "./a", "")] == [True, True, True, True, False, False, False]


def _architect_design(files):
    return "Design: add sub.\n```json\n" + json.dumps({"files": files}) + "\n```\n"


def test_a_list_whose_every_entry_escapes_is_a_typed_plan_refusal_before_any_developer_call(tmp_path):
    from _chaos_harness import (
        ChaosRuntime,
        RuntimeRegistration,
        benign_roles,
        chaos_config,
        chaos_engine,
        git_workspace,
        role_of,
        run_direct,
    )
    from test_prd032_chaos_model import CALC

    workspace = git_workspace(tmp_path, {"calc.py": CALC})
    roles = []

    def responder(role, request):
        roles.append(role)
        if role == "architect":
            return _architect_design(["../outside/evil.py", str(tmp_path / "abs.py")])
        return benign_roles(role, request)

    runtime = ChaosRuntime(responder)
    cfg = chaos_config()
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg), "add sub to calc.py", workspace)
    assert result["status"] == "architect_file_list_rejected"
    assert result["rejected_file_list_entries"] == ["../outside/evil.py", str(tmp_path / "abs.py")]
    assert "developer" not in roles and (workspace / "calc.py").read_text() == CALC
    assert not list((workspace / "outside").glob("*")) if (workspace / "outside").exists() else True
    del role_of
    with sqlite3.connect(trace_db_path(cfg)) as conn:
        [(status, category, events)] = conn.execute("SELECT status, failure_category, run_events FROM runs").fetchall()
    assert (status, category) == ("architect_file_list_rejected",) * 2
    [event] = [e for e in json.loads(events) if e["kind"] == "plan.file_list_entries_rejected"]
    assert event["details"] == {"rejected": ["../outside/evil.py", str(tmp_path / "abs.py")], "kept": []}


# ------------------------------------------------- MAVEN-ACQUISITION-CACHE-001 (TRACED: superseded by 5acf242)

def test_the_maven_dependency_cache_lives_under_the_state_root_and_survives_a_worktree_reset(tmp_path, monkeypatch):
    """The row inferred that a per-work-unit `git clean -fd` deleted the cache
    at <worktree>/.kriya/m2_cache. Since 5acf242 the cache is outside every
    source tree (<state>/dependency-cache/maven/<workspace key>): the reset
    cannot touch it, and a legacy in-tree cache migrates out on first use."""
    from kriya.core.state_paths import ENV_STATE_DIR
    from kriya.tools.validate import PolymorphicValidator

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))
    ws = _repo(tmp_path / "ws", {"pom.xml": "<project/>\n"})
    legacy = ws / ".kriya" / "m2_cache"
    legacy.mkdir(parents=True)
    (legacy / "seed.jar").write_bytes(b"jar")
    validator = PolymorphicValidator(str(ws))
    cache_dir = validator._maven_cache_dir()
    assert os.path.realpath(cache_dir).startswith(os.path.realpath(str(tmp_path / "state")) + os.sep)
    assert not os.path.realpath(cache_dir).startswith(os.path.realpath(str(ws)) + os.sep)
    assert not legacy.exists() and os.path.isfile(os.path.join(cache_dir, "seed.jar"))  # migrated out of the tree
    worktree = create_git_worktree(str(ws))
    subprocess.run(["git", "clean", "-fdx"], cwd=worktree, check=True, capture_output=True)
    assert os.path.isfile(os.path.join(cache_dir, "seed.jar"))
    assert validator._maven_cache_dir() == cache_dir  # stable per workspace across work units


# ------------------------------------------------- independent review reconciliation (reviews/INDEPENDENT_REVIEW.md)

def test_f1_an_existing_empty_original_is_captured_as_empty_bytes_never_as_missing():
    from kriya.workflow.state import GenerationState
    from kriya.workflow.workflow import _captured_originals

    state = GenerationState()
    state.all_files_written = {"pkg/__init__.py", "pkg/new.py", "pkg/mod.py", "legacy.py"}
    state.all_original_raw = {"pkg/__init__.py": b"", "pkg/new.py": None, "pkg/mod.py": b"x = 1\n"}
    state.all_original_contents = {"legacy.py": "old\n"}
    assert _captured_originals(state) == {"pkg/__init__.py": b"", "pkg/new.py": None, "pkg/mod.py": b"x = 1\n",
                                          "legacy.py": b"old\n"}


class _ReviewerInternalCrash(Transport):
    """A Kriya-side coding error inside the review path, not a backend failure."""

    def __init__(self):
        super().__init__()
        self.failed = 0

    async def __call__(self, client, model, system_prompt, user_prompt, *args, **kwargs):
        if current_model_role() == "reviewer":
            self.failed += 1
            raise RuntimeError("simulated coding error in the review path")
        return await super().__call__(client, model, system_prompt, user_prompt, *args, **kwargs)


def test_f3_a_non_backend_exception_in_the_final_review_is_attributed_as_internal(tmp_path, monkeypatch):
    from kriya.workflow.workflow import FINAL_REVIEW_INTERNAL_ERROR

    workspace = _workspace(tmp_path)
    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe)
    model_runtime.clear_model_runtime_cache()
    monkeypatch.chdir(workspace)
    cfg = _config(tmp_path)
    transport = _ReviewerInternalCrash()
    results = []
    real_run = WorkflowEngine.run_generation_workflow

    async def capturing(self, *a, **kw):
        result = await real_run(self, *a, **kw)
        results.append(result)
        return result

    with patch("kriya.cli.load_config", return_value=cfg), patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(WorkflowEngine, "run_generation_workflow", new=capturing):
        cli = CliRunner().invoke(main, ["generate", "build M1: create m1.py", "-y"])
    assert cli.exception is None or isinstance(cli.exception, SystemExit), cli.output
    assert cli.exit_code != 0 and transport.failed == 1
    refusal = results[-1]["final_review_refusal"]
    assert refusal["reason_code"] == FINAL_REVIEW_INTERNAL_ERROR and refusal["exception_type"] == "RuntimeError"
    status, category, events = _trace_rows(cfg)[-1]
    assert (status, category) == ("failure", "final_review_refused")
    [refused] = [e for e in json.loads(events) if e["kind"] == "review.refused"]
    assert "internal error" in refused["message"] and "model backend" not in refused["message"]
    assert (workspace / "m1.py").read_text().strip() == "VALUE = 'm1.py'"  # still applied and committed


def test_f8_a_sealing_failure_is_recorded_on_the_refusal_never_silent(caplog):
    from kriya.workflow import authority_request as ar
    from kriya.workflow.contract_compilation import compile_verification_contract
    from kriya.workflow.requirements import VerificationAuthorityRequired, statement_origins

    goal = "Entries must leave the cache at the expiry instant for every ttl.\n"
    reqs = derive_requirements(goal)
    contract = compile_verification_contract(reqs, origins=statement_origins(goal), test_files=[], project_language=None)
    admission = contract.refusal()
    assert isinstance(admission, VerificationAuthorityRequired) and admission.authority_requests
    cfg = strict_config(logging={"file_enabled": False})
    with patch.object(ar, "seal_authority_requests", side_effect=OSError("store is read-only")), caplog.at_level("WARNING"):
        assert ar.seal_requests_for_refusal(cfg, contract, admission) is None
    assert admission.authority_requests_sealing_error == "OSError: store is read-only"
    assert admission.to_dict()["authority_requests_sealing_error"] == "OSError: store is read-only"
    assert "Authority requests not sealed: OSError: store is read-only" in caplog.text
    # the normal path: sealed, no error recorded
    fresh = contract.refusal()
    with patch.object(ar, "seal_authority_requests", return_value="/outside/requests.json"):
        assert ar.seal_requests_for_refusal(cfg, contract, fresh) == "/outside/requests.json"
    assert fresh.to_dict()["authority_requests_sealing_error"] is None


# ------------------------------------------------- primary cohort findings (BACKEND-READINESS-004 live runs)

def _plan_with(files):
    from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, PlannedFile, Subtask
    from kriya.workflow.triage import ChangeKind

    return EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="add lower/upper/trim and their tests", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path=path, action=action) for path, action in files],
    )])


def test_t5_a_plan_editing_an_existing_test_under_a_test_immutability_claim_is_refused_before_any_unit(tmp_path):
    """P4-T5 (jmespath): the plan's s2 edited tests/test_functions.py although the goal required every existing test
    to keep passing unchanged; the correct functions were refused at the terminal gate (test immutability + the
    bundle's tamper gate) after 18 model calls. The constraint is now a planning-time refusal with repair guidance."""
    from kriya.workflow.contract_compilation import compile_verification_contract
    from kriya.workflow.plan_schema import FileAction
    from kriya.workflow.plan_validation import validate_plan
    from kriya.workflow.planner_repair import PLANNER_POLICY_REJECTION_CODES
    from kriya.workflow.requirements import statement_origins
    from kriya.workflow.workflow import immutable_test_files

    ws = _repo(tmp_path / "ws", {"jmespath/functions.py": "x = 1\n", "tests/test_functions.py": "def test_a():\n    assert True\n",
                                  "tests/test_lexer.py": "def test_b():\n    assert True\n"})
    goal = ("Add three built-in string functions: lower, upper and trim.\n\n"
            "Every existing public API and every existing test must keep passing unchanged.\n")
    reqs = derive_requirements(goal)
    contract = compile_verification_contract(reqs, origins=statement_origins(goal),
                                             test_files=["tests/test_functions.py", "tests/test_lexer.py"], project_language="python")
    immutable = immutable_test_files(contract, str(ws))
    assert immutable == ["tests/test_functions.py", "tests/test_lexer.py"]
    # the measured plan shape: an existing test file edited -> refused, typed, with repair guidance registered
    edited = _plan_with([("jmespath/functions.py", FileAction.MODIFY), ("tests/test_functions.py", FileAction.MODIFY)])
    result = asyncio.run(validate_plan(edited, workspace_path=str(ws), immutable_test_files=immutable))
    assert result.valid is False and "PLAN_EDITS_IMMUTABLE_TEST" in result.reason_codes
    assert "s1:tests/test_functions.py" in "".join(result.errors) and "new test file" in "".join(result.errors)
    assert "PLAN_EDITS_IMMUTABLE_TEST" in PLANNER_POLICY_REJECTION_CODES
    # a deletion is an edit too; a NEW test file is the allowed shape; no claim -> no constraint
    deleted = _plan_with([("tests/test_lexer.py", FileAction.DELETE)])
    assert asyncio.run(validate_plan(deleted, workspace_path=str(ws), immutable_test_files=immutable)).valid is False
    created = _plan_with([("jmespath/functions.py", FileAction.MODIFY), ("tests/test_string_functions.py", FileAction.CREATE)])
    assert asyncio.run(validate_plan(created, workspace_path=str(ws), immutable_test_files=immutable)).valid is True
    assert asyncio.run(validate_plan(edited, workspace_path=str(ws))).valid is True  # the pre-existing contract, unchanged
    free_goal = "Add three built-in string functions: lower, upper and trim.\n"
    free = compile_verification_contract(derive_requirements(free_goal), origins=statement_origins(free_goal),
                                         test_files=["tests/test_functions.py"], project_language="python")
    assert immutable_test_files(free, str(ws)) is None


def test_t2_a_goal_named_member_of_a_planned_owner_is_a_known_target_member_hint(tmp_path):
    """P4-T2 (jsoup): the subtask's only target, Element.java (21,042 tokens), had no member hint although the goal
    names Element.absUrl(...), so the whole file was the minimum unit, did not fit the 4,176-token protected room and
    the attempt stopped typed before any model call. The goal's own qualified names of the planned owner are now a
    grounded member-hint source, validated against the current member boundaries like a retrieval hint - applied
    ONLY as the capacity fallback (a target whose whole source does not fit); a target that fits whole keeps its
    full, authoritative source (the full-file rewrite tests of tests/test_full_file_final_newline.py)."""
    from test_dev_inv_001_investigation import _minimal_attempt_ctx

    from kriya.workflow.attempt import (
        _goal_named_member_hints,
        _goal_named_members_of,
        _resolve_known_target_member_hints,
        _target_package_with_goal_member_fallback,
    )

    filler = "".join(f"    public int helper{i}(int a) {{\n        return a + {i};\n    }}\n\n" for i in range(400))
    element = ("package org.jsoup.nodes;\n\npublic class Element extends Node {\n"
               "    public String attr(String attributeKey) {\n        return super.attr(attributeKey);\n    }\n\n"
               + filler +
               "    public String absUrl(String attributeKey) {\n        return StringUtil.resolve(baseUri(), attr(attributeKey));\n    }\n}\n")
    (tmp_path / "Element.java").write_text(element)
    (tmp_path / "Node.java").write_text("package org.jsoup.nodes;\n\npublic class Node {\n    public String attr(String k) {\n        return \"\";\n    }\n}\n")
    goal = ("Element.absUrl(...) / attr(\"abs:href\") resolves relative links incorrectly in two situations.\n\n"
            "Node.attr(\"abs:href\") must keep working; see Jsoup.parse(...).\n")
    assert _goal_named_members_of(goal, "Element") == ["absUrl"]
    assert _goal_named_members_of(goal, "Node") == ["attr"] and _goal_named_members_of(goal, "StringUtil") == []
    ctx = _minimal_attempt_ctx(tmp_path, goal=goal, retrieval_member_hints={})
    hints = _goal_named_member_hints(ctx, ["Element.java", "Node.java"])
    # owner-qualified members first; a member the goal names under another owner or as a bare call counts only when
    # THIS file defines it (P4-T2-r2: the plan targeted Node.java while the goal says Element.absUrl - Node defines absUrl)
    assert [h.split(".")[-1].split("(")[0] for h in hints["Element.java"]] == ["absUrl", "attr"]
    assert [h.split(".")[-1].split("(")[0] for h in hints["Node.java"]] == ["attr"]  # Node.java here defines no absUrl
    (tmp_path / "Node.java").write_text("package org.jsoup.nodes;\n\npublic class Node {\n    public String attr(String k) {\n        return \"\";\n    }\n\n    public String absUrl(String k) {\n        return k;\n    }\n}\n")
    assert sorted(h.split(".")[-1].split("(")[0] for h in _goal_named_member_hints(ctx, ["Node.java"])["Node.java"]) == ["absUrl", "attr"]
    # a goal-named member no boundary carries is dropped, never fabricated; retrieval hints are untouched by the goal
    assert _goal_named_member_hints(_minimal_attempt_ctx(tmp_path, goal="Element.vanish(...) is broken.",
                                                         retrieval_member_hints={}), ["Element.java"]) == {}
    assert _resolve_known_target_member_hints(ctx, ["Element.java"]) == {}
    # the measured shape: Element.java's whole source is unfit for the room -> the fallback adds absUrl and rebuilds
    rendered, package, fallback = _target_package_with_goal_member_fallback(ctx, ["Element.java"], 900, 8000, {})
    assert fallback is not None and fallback["unfit_targets"] == ["Element.java"]
    assert [m.split(".")[-1].split("(")[0] for m in fallback["goal_member_hints"]["Element.java"]] == ["absUrl"]
    exact = [i for i in package.relevant_files if i.path == "Element.java" and i.tier == "member_exact" and i.is_exact]
    assert exact and "StringUtil.resolve(baseUri()" in rendered
    # a small target that fits whole is shown whole and exact, never demoted: no fallback, full tier
    (tmp_path / "Node.java").write_text("package org.jsoup.nodes;\n\npublic class Node {\n    public String attr(String k) {\n        return \"\";\n    }\n}\n")
    rendered, package, fallback = _target_package_with_goal_member_fallback(ctx, ["Node.java"], 900, 8000, {})
    assert fallback is None and [i.tier for i in package.relevant_files if i.path == "Node.java"] == ["full"]
