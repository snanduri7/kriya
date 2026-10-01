"""WORKFLOW-RECOVERY-HANDBACK-001: API contract recovery owns restoration only.

PRD-036 rc5 matrix trial 3 (C6): the primary Developer's first candidate
removed an established public signature, the pre-write gate rejected it,
recovery restored the owner deterministically and then spent its dedicated
attempts on the primary, and the run stopped with the full-set budget and
the configured fallback never tried. These tests drive the real
run_generation_workflow() with a scripted model and a real git workspace.
"""
import asyncio
import json
import os
import sqlite3
import subprocess
from unittest.mock import MagicMock

from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.state_paths import trace_db_path
from kriya.workflow import attempt as attempt_module
from kriya.workflow.file_resolution import IncompleteGenerationError
from kriya.workflow.retry_policy import API_CONTRACT_RECOVERY_MAX_ATTEMPTS
from kriya.workflow.workflow import WorkflowEngine

PRIMARY = "primary-coder:30b"
FALLBACK = "fallback-coder:35b"
BASELINE = "def add(a, b):\n    return a + b\n"
GOOD = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
# The primary's first candidate drops add(a, b): a public API removal.
DROPS_ADD = "def sub(a, b):\n    return a - b\n"
GOAL = "Add a function sub(a, b) returning a - b to calc.py."


def _failing_repair(n):
    # add(a, b)'s signature intact (the contract holds) but test_add fails;
    # distinct per call, so no two attempts share a workspace.
    return f"# attempt {n}\ndef add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"


PRIMARY_OUTPUTS = [DROPS_ADD] + [_failing_repair(n) for n in range(40)]


def _workspace(tmp_path):
    (tmp_path / "calc.py").write_text(BASELINE)
    (tmp_path / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    for argv in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-q", "-m", "initial"]):
        subprocess.run(["git", *argv], cwd=str(tmp_path), check=True)
    return str(tmp_path)


def _engine(chain=True):
    cfg = AppConfig()
    cfg.autonomy.developer_response_protocol = "legacy_strict"  # these tests replay legacy/raw Developer responses
    cfg.llm.model = PRIMARY
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model=FALLBACK, base_url=cfg.llm.base_url)] if chain else []
    llm = LLMClient(cfg)
    return WorkflowEngine(Kernel(config=cfg), llm), llm, cfg


def _script(llm, workspace):
    """Planner, Architect, then the Developer: the primary's outputs in order,
    the fallback always GOOD; every other role gets a neutral answer. Records
    each Developer call's model and the workspace's calc.py at that moment."""
    developer_calls, seen_on_disk = [], []
    calls = {"n": 0}
    primary = iter(PRIMARY_OUTPUTS)

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        calls["n"] += 1
        text = f"{system_prompt}\n{user_prompt}"
        if calls["n"] == 1:
            return "Step 1: add sub(a, b) to calc.py"
        if calls["n"] == 2:
            return json.dumps({"files": ["calc.py"]})
        if not system_prompt.startswith(("You are the Kriya Developer Agent", "You are the Kriya File List Planner")):
            return json.dumps({"verdict": "PASS", "requirements": []})
        model = kwargs.get("model_override") or PRIMARY
        developer_calls.append(model)
        with open(os.path.join(workspace, "calc.py")) as handle:
            seen_on_disk.append(handle.read())
        content = GOOD if model == FALLBACK else next(primary)
        if "FILE CONTENT:" in text:  # a repair request: the marker response shape
            return f"FIX ANALYSIS: repair calc.py\nFILE CONTENT:\n{content}"
        return json.dumps([{"filepath": "calc.py", "content": content}])

    llm.complete = complete
    return developer_calls, seen_on_disk


def _run(engine, workspace):
    return asyncio.run(engine.run_generation_workflow(
        goal=GOAL, workspace_path=workspace, approval_callback=MagicMock(return_value=True),
    ))


def _trace(cfg):
    conn = sqlite3.connect(trace_db_path(cfg))
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone())
    conn.close()
    return json.loads(row["run_events"] or "[]"), json.loads(row["model_hops"] or "[]")


def _read(workspace):
    with open(os.path.join(workspace, "calc.py")) as handle:
        return handle.read()


def test_a_restored_contract_hands_an_exhausted_recovery_back_to_the_configured_fallback(tmp_path):
    """The exact rc5 C6 path: primary violates the API -> pre-write rejection
    -> recovery restores the contract -> recovery's budget is spent on the
    primary -> the ordinary policy reaches the fallback -> it succeeds."""
    workspace = _workspace(tmp_path)
    engine, llm, cfg = _engine()
    developer_calls, seen_on_disk = _script(llm, workspace)

    result = _run(engine, workspace)

    assert result["quality_gates_passed"] is True
    assert result["failure_category"] is None
    assert _read(workspace) == GOOD
    # Recovery spent its own budget first: the rejected attempt, then one
    # primary repair per remaining recovery attempt (the restore itself
    # calls no model), and only then the fallback.
    first_fallback = developer_calls.index(FALLBACK)
    assert developer_calls[:first_fallback] == [PRIMARY] * API_CONTRACT_RECOVERY_MAX_ATTEMPTS
    # The rejected candidate never reached the real workspace.
    assert DROPS_ADD not in seen_on_disk
    assert all(content == BASELINE for content in seen_on_disk)
    assert result["retry_progress"]["no_progress_terminated"] is False

    events, hops = _trace(cfg)
    advanced = [e for e in events if e["kind"] == "api_contract_recovery.phase_advanced"]
    assert [(e["details"]["source_phase"], e["details"]["target_phase"]) for e in advanced] == [
        ("RESTORE_PUBLIC_CONTRACT", "REPAIR_BEHAVIOR"),
    ]
    transitions = [e["details"] for e in events if e["kind"] == "model.transition" and e["details"]["fallback"]]
    assert transitions and transitions[0]["from"]["model"] == PRIMARY
    assert transitions[0]["to"]["model"] == FALLBACK
    assert FALLBACK in json.dumps(hops)


def test_with_no_fallback_the_handback_ends_in_a_bounded_failure(tmp_path):
    workspace = _workspace(tmp_path)
    engine, llm, _ = _engine(chain=False)
    developer_calls, seen_on_disk = _script(llm, workspace)

    result = _run(engine, workspace)

    assert result["quality_gates_passed"] is False
    assert result["failure_category"] == "quality_gates_exhausted"
    assert FALLBACK not in developer_calls
    # The ordinary families continued after the handback, within their own
    # budgets (full-set 4 + targeted 3 + recovery 3 bounds the attempts).
    assert API_CONTRACT_RECOVERY_MAX_ATTEMPTS < len(developer_calls) <= 4 + 3 + API_CONTRACT_RECOVERY_MAX_ATTEMPTS
    assert _read(workspace) == BASELINE
    assert DROPS_ADD not in seen_on_disk


def test_an_unrestorable_contract_stops_fail_closed_and_never_calls_the_fallback(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    engine, llm, _ = _engine()
    developer_calls, seen_on_disk = _script(llm, workspace)
    restore_calls = []

    def cannot_restore(state, contract):
        restore_calls.append(contract.owner_files)
        raise IncompleteGenerationError(["calc.py"], "no captured baseline content for calc.py")

    monkeypatch.setattr(attempt_module, "_restore_api_contract_owners_deterministically", cannot_restore)

    result = _run(engine, workspace)

    assert result["quality_gates_passed"] is False
    assert result["failure_category"] == "quality_gates_exhausted"
    assert len(restore_calls) == API_CONTRACT_RECOVERY_MAX_ATTEMPTS
    assert developer_calls == [PRIMARY]  # only the rejected first attempt; no fallback, no ordinary retry
    assert _read(workspace) == BASELINE
    assert DROPS_ADD not in seen_on_disk
